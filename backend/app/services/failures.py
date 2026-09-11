from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.models import StepRun, StepStatus, WorkflowRun, WorkflowStatus


def record_failure(
    engine: Engine, run_id: UUID, step_id: UUID, error: Exception
) -> StepStatus:
    """Persist a handler failure; the attempt was already counted when claimed."""
    with Session(engine) as session, session.begin():
        run = session.scalar(
            select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update()
        )
        step = session.get(StepRun, step_id)
        if run is None or step is None or step.status != StepStatus.RUNNING:
            raise RuntimeError(f"Cannot record failure for step {step_id}")
        retryable = step.attempt_count < step.max_attempts
        step.status = StepStatus.RETRYING if retryable else StepStatus.FAILED
        step.error = f"{type(error).__name__}: {error}".replace("\x00", "\\0")
        step.output = None
        step.completed_at = None if retryable else datetime.now(timezone.utc)
        if not retryable and run.status in (
            WorkflowStatus.PENDING,
            WorkflowStatus.RUNNING,
        ):
            run.status = WorkflowStatus.FAILED
            run.completed_at = datetime.now(timezone.utc)
        return step.status
