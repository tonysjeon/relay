"""FIFO step identifiers; PostgreSQL remains the source of workflow state."""

import json
from collections.abc import Sequence
from uuid import UUID

from redis import Redis

JOB_QUEUE = "relay:jobs"


def enqueue_steps(
    redis: Redis, step_run_ids: Sequence[UUID], *, queue_name: str = JOB_QUEUE
) -> None:
    # A single RPUSH appends the complete batch atomically within Redis.
    messages = [
        json.dumps({"step_run_id": str(UUID(str(step_id)))}) for step_id in step_run_ids
    ]
    if messages:
        redis.rpush(queue_name, *messages)


def enqueue_step(
    redis: Redis, step_run_id: UUID, *, queue_name: str = JOB_QUEUE
) -> None:
    enqueue_steps(redis, [step_run_id], queue_name=queue_name)


def dequeue_step(redis: Redis, *, queue_name: str = JOB_QUEUE) -> UUID | None:
    """Pop one identifier without blocking; malformed messages raise ValueError."""
    message = redis.lpop(queue_name)
    if message is None:
        return None
    try:
        payload = json.loads(message)
        if not isinstance(payload, dict) or set(payload) != {"step_run_id"}:
            raise ValueError("Job must contain only step_run_id")
        if not isinstance(payload["step_run_id"], str):
            raise TypeError("step_run_id must be a UUID string")
        return UUID(payload["step_run_id"])
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid step queue message") from exc
