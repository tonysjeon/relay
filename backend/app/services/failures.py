from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from app.models import StepRun, StepStatus, WorkflowRun
from app.services.attempts import finish_attempt
from app.services.leases import lease_conditions
from app.services.retries import retry_delay
from app.services.workflow_status import refresh_workflow_status


def record_failure(
    engine: Engine,
    run_id: UUID,
    step_id: UUID,
    error: Exception,
    *,
    worker_id: str,
    attempt: int,
) -> StepStatus | None:
    """Persist a handler failure; the attempt was already counted when claimed."""
    with Session(engine) as session, session.begin():
        run = session.scalar(
            select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update()
        )
        step = session.get(StepRun, step_id)
        if run is None or step is None:
            return None
        retryable = step.attempt_count < step.max_attempts
        status = StepStatus.RETRYING if retryable else StepStatus.FAILED
        next_retry_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=retry_delay(step.attempt_count))
            if retryable
            else None
        )
        changed = session.execute(
            update(StepRun)
            .where(*lease_conditions(step_id, worker_id, attempt))
            .values(
                status=status,
                error=f"{type(error).__name__}: {error}".replace("\x00", "\\0"),
                output=None,
                completed_at=None if retryable else datetime.now(timezone.utc),
                next_retry_at=next_retry_at,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        if changed.rowcount != 1:
            return None
        finish_attempt(
            session,
            step_id,
            attempt,
            worker_id,
            "FAILED",
            error=f"{type(error).__name__}: {error}".replace("\x00", "\\0"),
        )
        refresh_workflow_status(session, run)
        return status
