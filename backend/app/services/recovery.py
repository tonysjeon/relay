import json
import logging

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.models import StepRun, StepStatus, WorkflowRun, WorkflowStatus
from app.services.attempts import finish_attempt
from app.services.queue import JOB_QUEUE, enqueue_steps
from app.services.workflow_status import refresh_workflow_status
from app.services.workflows import QueueDispatchError

logger = logging.getLogger(__name__)


def recover_abandoned_steps(
    engine: Engine, redis: Redis, *, queue_name: str = JOB_QUEUE
) -> int:
    expired = (
        StepRun.status == StepStatus.RUNNING,
        StepRun.lease_expires_at <= func.clock_timestamp(),
    )
    with Session(engine) as session:
        run_ids = list(
            session.scalars(
                select(WorkflowRun.id)
                .where(
                    WorkflowRun.queue_name == queue_name,
                    select(StepRun.id)
                    .where(StepRun.workflow_run_id == WorkflowRun.id, *expired)
                    .exists(),
                )
                .order_by(WorkflowRun.id)
                .limit(100)
            )
        )
    requeued = 0
    for run_id in run_ids:
        with Session(engine) as session, session.begin():
            run = session.scalar(
                select(WorkflowRun)
                .where(WorkflowRun.id == run_id)
                .with_for_update(skip_locked=True)
            )
            if run is None:
                continue
            steps = list(
                session.scalars(
                    select(StepRun)
                    .where(StepRun.workflow_run_id == run_id, *expired)
                    .with_for_update(skip_locked=True)
                )
            )
            now = session.scalar(select(func.clock_timestamp()))
            ready_ids, events = [], []
            for step in steps:
                retryable = (
                    run.status in (WorkflowStatus.PENDING, WorkflowStatus.RUNNING)
                    and step.attempt_count < step.max_attempts
                )
                events.append(
                    {
                        "event": "step_abandoned",
                        "workflow_run_id": str(run_id),
                        "step_run_id": str(step.id),
                        "worker_id": step.lease_owner,
                        "attempt": step.attempt_count,
                        "requeued": retryable,
                    }
                )
                finish_attempt(
                    session,
                    step.id,
                    step.attempt_count,
                    step.lease_owner,
                    "ABANDONED",
                    error="Worker lease expired",
                )
                step.status = StepStatus.READY if retryable else StepStatus.FAILED
                step.error = "Worker lease expired"
                step.output = None
                step.lease_owner = None
                step.lease_expires_at = None
                step.next_retry_at = None
                step.completed_at = None if retryable else now
                # The abandoned claim already consumed an attempt. The next
                # worker increments the counter when it actually claims again.
                if retryable:
                    ready_ids.append(step.id)
            if steps:
                refresh_workflow_status(session, run)
            if run.status != WorkflowStatus.RUNNING:
                ready_ids = []
        for event in events:
            logger.warning(json.dumps(event))
        if ready_ids:
            try:
                enqueue_steps(redis, ready_ids, queue_name=queue_name)
            except RedisError as exc:
                raise QueueDispatchError(run_id, ready_ids) from exc
            requeued += len(ready_ids)
    return requeued
