"""Attempt history shares the transaction and fencing of step state changes."""

from uuid import UUID

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.models import StepAttempt


def finish_attempt(
    session: Session,
    step_id: UUID,
    attempt: int,
    worker_id: str | None,
    status: str,
    *,
    error: str | None = None,
) -> None:
    # Call only after a fenced transition or while holding the recovery locks.
    # Older steps may have no history; never fabricate an earlier attempt.
    session.execute(
        update(StepAttempt)
        .where(
            StepAttempt.step_run_id == step_id,
            StepAttempt.attempt_number == attempt,
            StepAttempt.worker_id == worker_id,
            StepAttempt.status == "RUNNING",
        )
        .values(status=status, error=error, completed_at=func.clock_timestamp())
    )
