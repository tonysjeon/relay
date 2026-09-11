from typing import Any
from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.connections import create_database_engine, create_redis_client
from app.models import StepStatus
from app.services.queue import JOB_QUEUE, enqueue_steps
from app.services.runs import (
    create_step_dependencies,
    create_step_runs,
    create_workflow_run,
)
from app.workflows import Workflow


class QueueDispatchError(RuntimeError):
    """The run committed, but Redis did not confirm queueing its ready steps."""

    def __init__(self, run_id: UUID, step_run_ids: list[UUID]):
        self.run_id = run_id
        self.step_run_ids = tuple(step_run_ids)
        super().__init__(f"Workflow run {run_id} was saved but queue dispatch failed")


def run(
    workflow: Workflow,
    input: dict[str, Any],
    *,
    engine: Engine | None = None,
    redis: Redis | None = None,
    queue_name: str = JOB_QUEUE,
) -> UUID:
    """Validate, commit a complete run, then enqueue its initially ready steps.

    Use DATABASE_URL by default, or supply a reusable engine owned by the caller.
    Persistence errors roll back the run. QueueDispatchError identifies a saved
    run whose queue dispatch needs retrying. Supplied connections are caller-owned.
    """
    workflow.validate()
    owned_engine = engine is None
    if engine is None:
        engine = create_database_engine(Settings())
    try:
        with Session(engine) as session, session.begin():
            workflow_run = create_workflow_run(session, workflow.name, input)
            steps = create_step_runs(session, workflow_run.id, list(workflow.steps))
            for step in steps:
                definition = workflow.steps[step.step_name]
                step.max_attempts = definition.max_attempts
                step.status = (
                    StepStatus.PENDING if definition.depends_on else StepStatus.READY
                )
            create_step_dependencies(
                session,
                workflow_run.id,
                {name: step.depends_on for name, step in workflow.steps.items()},
            )
            run_id = workflow_run.id
            ready_ids = [step.id for step in steps if step.status == StepStatus.READY]
        owned_redis = redis is None
        try:
            if redis is None:
                redis = create_redis_client(Settings())
            enqueue_steps(redis, ready_ids, queue_name=queue_name)
        except (RedisError, ValueError) as exc:
            raise QueueDispatchError(run_id, ready_ids) from exc
        finally:
            if owned_redis and redis is not None:
                redis.close()
        return run_id
    finally:
        if owned_engine:
            engine.dispose()
