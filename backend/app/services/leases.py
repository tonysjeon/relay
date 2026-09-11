import logging
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread
from uuid import UUID

from sqlalchemy import Engine, func, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import StepRun, StepStatus

logger = logging.getLogger(__name__)


def lease_conditions(step_id: UUID, worker_id: str, attempt: int):
    return (
        StepRun.id == step_id,
        StepRun.status == StepStatus.RUNNING,
        StepRun.lease_owner == worker_id,
        StepRun.attempt_count == attempt,
        StepRun.lease_expires_at > func.clock_timestamp(),
    )


def renew_lease(
    engine: Engine, step_id: UUID, worker_id: str, attempt: int, seconds: float
) -> bool:
    with Session(engine) as session, session.begin():
        renewed = session.execute(
            update(StepRun)
            .where(*lease_conditions(step_id, worker_id, attempt))
            .values(
                lease_expires_at=func.clock_timestamp() + timedelta(seconds=seconds)
            )
        )
        return renewed.rowcount == 1


@contextmanager
def keep_lease(
    engine: Engine, step_id: UUID, worker_id: str, attempt: int, seconds: float
):
    """Renew during synchronous execution; signal loss without trying to kill user code."""
    stop, lost = Event(), Event()

    def renew():
        while not stop.wait(seconds / 3):
            try:
                if not renew_lease(engine, step_id, worker_id, attempt, seconds):
                    lost.set()
                    return
            except SQLAlchemyError:
                logger.exception("Lease renewal failed for step %s", step_id)
                lost.set()
                return

    thread = Thread(target=renew, name="step-lease", daemon=True)
    thread.start()
    try:
        yield lost
    finally:
        stop.set()
        thread.join()
