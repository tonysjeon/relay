from datetime import datetime, timezone

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from app.models import StepRun, StepStatus, WorkflowRun, WorkflowStatus
from app.services.queue import JOB_QUEUE, enqueue_steps
from app.services.workflows import QueueDispatchError


def retry_delay(attempt_count: int) -> int:
    """Seconds after a failed attempt: 1, 2, 4, 8, 16, 32, then 60."""
    if attempt_count < 1:
        raise ValueError("attempt_count must be positive")
    return min(2 ** min(attempt_count - 1, 6), 60)


def schedule_retries(
    engine: Engine,
    redis: Redis,
    *,
    queue_name: str = JOB_QUEUE,
    now: datetime | None = None,
) -> int:
    cutoff = now if now is not None else datetime.now(timezone.utc)
    due = (
        StepRun.status == StepStatus.RETRYING,
        StepRun.next_retry_at <= cutoff,
        StepRun.attempt_count < StepRun.max_attempts,
    )
    with Session(engine) as session:
        run_ids = list(
            session.scalars(
                select(WorkflowRun.id)
                .where(
                    WorkflowRun.status == WorkflowStatus.RUNNING,
                    select(StepRun.id)
                    .where(StepRun.workflow_run_id == WorkflowRun.id, *due)
                    .exists(),
                )
                .order_by(WorkflowRun.id)
                .limit(100)
            )
        )
    dispatched = 0
    for run_id in run_ids:
        with Session(engine) as session, session.begin():
            # Use the same lock order as completion/failure handling. Recheck
            # state after locking, including when another scheduler got here first.
            run = session.scalar(
                select(WorkflowRun)
                .where(
                    WorkflowRun.id == run_id,
                    WorkflowRun.status == WorkflowStatus.RUNNING,
                )
                .with_for_update(skip_locked=True)
            )
            if run is None:
                continue
            ready_ids = list(
                session.scalars(
                    update(StepRun)
                    .where(StepRun.workflow_run_id == run_id, *due)
                    .values(status=StepStatus.READY, next_retry_at=None)
                    .returning(StepRun.id)
                    .execution_options(synchronize_session=False)
                )
            )
        if ready_ids:
            try:
                enqueue_steps(redis, ready_ids, queue_name=queue_name)
            except RedisError as exc:
                raise QueueDispatchError(run_id, ready_ids) from exc
            dispatched += len(ready_ids)
    return dispatched
