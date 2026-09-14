"""Durable decisions serialized with execution and cancellation."""

from datetime import datetime, timezone

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.connections import create_redis_client
from app.models import StepRun, StepStatus, WorkflowRun, WorkflowStatus
from app.services.dependencies import unlock_dependents
from app.services.queue import enqueue_steps
from app.services.workflow_api import WorkflowConflict, WorkflowNotFound
from app.services.workflow_status import refresh_workflow_status


def decide_approval(engine, run_id, step_id, decision, note="", *, redis=None):
    if decision not in ("approved", "rejected"):
        raise ValueError("Invalid approval decision")
    with Session(engine) as session, session.begin():
        run = session.scalar(
            select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update()
        )
        if run is None:
            raise WorkflowNotFound("Workflow run not found")
        step = session.get(StepRun, step_id, populate_existing=True)
        if step is None or step.workflow_run_id != run_id:
            raise WorkflowNotFound("Step not found")
        # Retrying the exact request is safe, including after workflow completion.
        if step.approval_decision == decision and step.approval_note == note:
            return
        if run.status != WorkflowStatus.RUNNING:
            raise WorkflowConflict("This workflow no longer accepts approvals")
        if step.status != StepStatus.WAITING_APPROVAL:
            raise WorkflowConflict("This step is not waiting for approval")
        step.approval_decision = decision
        step.approval_note = note
        step.approval_decided_at = datetime.now(timezone.utc)
        step.completed_at = step.approval_decided_at
        step.status = (
            StepStatus.COMPLETED if decision == "approved" else StepStatus.FAILED
        )
        step.error = None if decision == "approved" else "Rejected during human review"
        session.flush()
        ready_ids = (
            unlock_dependents(session, step_id) if decision == "approved" else []
        )
        refresh_workflow_status(session, run)
        queue_name = run.queue_name
    if ready_ids:
        owned = redis is None
        try:
            if redis is None:
                redis = create_redis_client(Settings())
            enqueue_steps(redis, ready_ids, queue_name=queue_name)
        except (RedisError, ValueError):
            # The decision committed. The scheduler redispatches persisted READY work.
            pass
        finally:
            if owned and redis is not None:
                redis.close()
