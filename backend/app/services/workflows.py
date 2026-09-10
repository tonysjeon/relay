from typing import Any
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.connections import create_database_engine
from app.models import StepStatus
from app.services.runs import (
    create_step_dependencies,
    create_step_runs,
    create_workflow_run,
)
from app.workflows import Workflow


def run(
    workflow: Workflow,
    input: dict[str, Any],
    *,
    engine: Engine | None = None,
) -> UUID:
    """Validate and commit a complete run without executing or queueing steps.

    Use DATABASE_URL by default, or supply a reusable engine owned by the caller.
    Persistence errors roll back the entire run and propagate to the caller.
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
        return run_id
    finally:
        if owned_engine:
            engine.dispose()
