"""Repair lost READY deliveries using PostgreSQL as the source of truth."""

import json

from redis import Redis
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.models import StepRun, StepStatus, WorkflowRun, WorkflowStatus
from app.services.queue import JOB_QUEUE

# Atomic membership check prevents repeated scans building a backlog while workers
# are offline. A concurrent pop may still cause redelivery; database claims fence it.
_ENQUEUE_MISSING = """
local added = 0
for _, message in ipairs(ARGV) do
    if not redis.call('LPOS', KEYS[1], message) then
        redis.call('RPUSH', KEYS[1], message)
        added = added + 1
    end
end
return added
"""


def reconcile_ready_steps(
    engine: Engine, redis: Redis, *, queue_name: str = JOB_QUEUE
) -> int:
    """Visit every eligible step in bounded batches, without changing attempts."""
    cursor = None
    added = 0
    while True:
        with Session(engine) as session:
            query = (
                select(StepRun.id)
                .join(WorkflowRun, WorkflowRun.id == StepRun.workflow_run_id)
                .where(
                    WorkflowRun.queue_name == queue_name,
                    WorkflowRun.status.in_(
                        [WorkflowStatus.PENDING, WorkflowStatus.RUNNING]
                    ),
                    StepRun.status == StepStatus.READY,
                )
                .order_by(StepRun.id)
                .limit(100)
            )
            if cursor is not None:
                query = query.where(StepRun.id > cursor)
            ids = list(session.scalars(query))
        if not ids:
            return added
        messages = [json.dumps({"step_run_id": str(step_id)}) for step_id in ids]
        added += redis.eval(_ENQUEUE_MISSING, 1, queue_name, *messages)
        cursor = ids[-1]
