"""Execute a ready step and dispatch newly unlocked dependents."""

import inspect
import json
import logging
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.connections import create_redis_client
from app.models import (
    StepAttempt,
    StepDependency,
    StepRun,
    StepStatus,
    WorkflowRun,
    WorkflowStatus,
)
from app.services.attempts import finish_attempt
from app.services.dependencies import unlock_dependents
from app.services.failures import record_failure
from app.services.leases import keep_lease, lease_conditions
from app.services.queue import JOB_QUEUE, enqueue_steps
from app.services.workflow_status import refresh_workflow_status
from app.services.workflows import QueueDispatchError
from app.workflows import Workflow

logger = logging.getLogger(__name__)


def execute_step(
    engine: Engine,
    step_run_id: UUID,
    registry: Mapping[str, Workflow],
    *,
    redis: Redis | None = None,
    queue_name: str = JOB_QUEUE,
    worker_id: str | None = None,
    lease_seconds: float | None = None,
) -> bool:
    """Return False for missing/unready work; persist handler failures and continue."""
    owner = worker_id if worker_id is not None else f"worker-inline-{uuid4()}"
    duration = (
        lease_seconds if lease_seconds is not None else Settings().step_lease_seconds
    )
    if duration <= 0:
        raise ValueError("lease_seconds must be positive")
    with Session(engine) as session, session.begin():
        step = session.get(StepRun, step_run_id)
        if step is None or step.status != StepStatus.READY:
            return False
        run = session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.id == step.workflow_run_id)
            .with_for_update()
        )
        if run is None:
            return False
        if run.status not in (WorkflowStatus.PENDING, WorkflowStatus.RUNNING):
            return False
        workflow = registry.get(run.workflow_name)
        if workflow is None or step.step_name not in workflow.steps:
            raise LookupError(
                f"No registered handler for {run.workflow_name}.{step.step_name}"
            )
        handler = workflow.steps[step.step_name].handler
        if inspect.iscoroutinefunction(handler):
            raise TypeError("The basic worker requires synchronous handlers")
        dependencies = list(
            session.scalars(
                select(StepRun)
                .join(
                    StepDependency, StepDependency.depends_on_step_run_id == StepRun.id
                )
                .where(StepDependency.step_run_id == step.id)
            )
        )
        if any(parent.status != StepStatus.COMPLETED for parent in dependencies):
            raise ValueError(f"Step {step.id} has incomplete dependencies")
        context = {
            "idempotency_key": f"{run.id}:{step.step_name}",
            "workflow_input": run.input,
            "step_outputs": {
                parent.step_name: parent.output for parent in dependencies
            },
        }
        claimed = session.execute(
            update(StepRun)
            .where(StepRun.id == step.id, StepRun.status == StepStatus.READY)
            .values(
                status=StepStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
                attempt_count=StepRun.attempt_count + 1,
                input=context,
                lease_owner=owner,
                lease_expires_at=func.clock_timestamp() + timedelta(seconds=duration),
            )
            .returning(StepRun.id, StepRun.attempt_count, StepRun.started_at)
        ).one_or_none()
        if claimed is None:
            return False
        session.add(
            StepAttempt(
                step_run_id=step.id,
                attempt_number=claimed.attempt_count,
                worker_id=owner,
                status="RUNNING",
                started_at=claimed.started_at,
            )
        )
        session.execute(
            update(WorkflowRun)
            .where(
                WorkflowRun.id == run.id, WorkflowRun.status == WorkflowStatus.PENDING
            )
            .values(
                status=WorkflowStatus.RUNNING, started_at=datetime.now(timezone.utc)
            )
        )
        log_fields = {
            "workflow_run_id": str(run.id),
            "step_run_id": str(step.id),
            "step_name": step.step_name,
            "attempt": claimed.attempt_count,
            "worker_id": owner,
        }
        workflow_run_id = run.id
        queue_name = run.queue_name
        attempt = claimed.attempt_count

    # Commit the claim and release the connection before invoking user code.
    logger.info(json.dumps({"event": "step_started", **log_fields}))
    with keep_lease(engine, step_run_id, owner, attempt, duration) as lost:
        try:
            output = handler(context)
            json.dumps(output, allow_nan=False)
        except Exception as exc:  # noqa: BLE001 -- isolate arbitrary user handlers
            if lost.is_set():
                return False
            status = record_failure(
                engine,
                workflow_run_id,
                step_run_id,
                exc,
                worker_id=owner,
                attempt=attempt,
            )
            if status is None:
                return False
            logger.warning(
                json.dumps(
                    {"event": "step_failed", "status": status.value, **log_fields}
                )
            )
            return True
        if lost.is_set():
            logger.warning(json.dumps({"event": "lease_lost", **log_fields}))
            return False
        with Session(engine) as session, session.begin():
            # Serialize completion for this run, not handler execution. The next
            # completion sees the previous one's committed prerequisite status.
            run = session.scalar(
                select(WorkflowRun)
                .where(WorkflowRun.id == workflow_run_id)
                .with_for_update()
            )
            if run is None:
                raise LookupError(f"Workflow run {workflow_run_id} no longer exists")
            completed = session.execute(
                update(StepRun)
                .where(*lease_conditions(step_run_id, owner, attempt))
                .values(
                    status=StepStatus.COMPLETED,
                    output=output,
                    error=None,
                    next_retry_at=None,
                    lease_owner=None,
                    lease_expires_at=None,
                    completed_at=datetime.now(timezone.utc),
                )
            )
            if completed.rowcount != 1:
                logger.warning(json.dumps({"event": "lease_lost", **log_fields}))
                return False
            finish_attempt(session, step_run_id, attempt, owner, "COMPLETED")
            ready_ids = (
                unlock_dependents(session, step_run_id)
                if run.status == WorkflowStatus.RUNNING
                else []
            )
            refresh_workflow_status(session, run)
        logger.info(json.dumps({"event": "step_completed", **log_fields}))
    if ready_ids:
        owned_redis = redis is None
        try:
            if redis is None:
                redis = create_redis_client(Settings())
            enqueue_steps(redis, ready_ids, queue_name=queue_name)
        except (RedisError, ValueError) as exc:
            raise QueueDispatchError(workflow_run_id, ready_ids) from exc
        finally:
            if owned_redis and redis is not None:
                redis.close()
    return True
