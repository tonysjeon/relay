"""Execute one ready step. Dependency scheduling and recovery are later phases."""

import inspect
import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from app.models import StepDependency, StepRun, StepStatus, WorkflowRun, WorkflowStatus
from app.workflows import Workflow

logger = logging.getLogger(__name__)


def execute_step(
    engine: Engine, step_run_id: UUID, registry: Mapping[str, Workflow]
) -> bool:
    """Return False for missing/unready work; propagate execution failures for now."""
    with Session(engine) as session, session.begin():
        step = session.get(StepRun, step_run_id)
        if step is None or step.status != StepStatus.READY:
            return False
        run = session.get(WorkflowRun, step.workflow_run_id)
        if run.status not in (WorkflowStatus.PENDING, WorkflowStatus.RUNNING):
            return False
        workflow = registry.get(run.workflow_name)
        if workflow is None or step.step_name not in workflow.steps:
            raise LookupError(
                f"No registered handler for {run.workflow_name}.{step.step_name}"
            )
        handler = workflow.steps[step.step_name].handler
        if inspect.iscoroutinefunction(handler):
            raise TypeError("The basic worker requires synchronous handlers")
        dependencies = list(
            session.scalars(
                select(StepRun)
                .join(
                    StepDependency, StepDependency.depends_on_step_run_id == StepRun.id
                )
                .where(StepDependency.step_run_id == step.id)
            )
        )
        if any(parent.status != StepStatus.COMPLETED for parent in dependencies):
            raise ValueError(f"Step {step.id} has incomplete dependencies")
        context = {
            "workflow_input": run.input,
            "step_outputs": {
                parent.step_name: parent.output for parent in dependencies
            },
        }
        claimed = session.execute(
            update(StepRun)
            .where(StepRun.id == step.id, StepRun.status == StepStatus.READY)
            .values(
                status=StepStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
                attempt_count=StepRun.attempt_count + 1,
                input=context,
            )
            .returning(StepRun.id)
        ).scalar_one_or_none()
        if claimed is None:
            return False
        session.execute(
            update(WorkflowRun)
            .where(
                WorkflowRun.id == run.id, WorkflowRun.status == WorkflowStatus.PENDING
            )
            .values(
                status=WorkflowStatus.RUNNING, started_at=datetime.now(timezone.utc)
            )
        )
        log_fields = {
            "workflow_run_id": str(run.id),
            "step_run_id": str(step.id),
            "step_name": step.step_name,
            "attempt": step.attempt_count,
        }

    # Commit the claim and release the connection before invoking user code.
    logger.info(json.dumps({"event": "step_started", **log_fields}))
    output = handler(context)
    json.dumps(
        output, allow_nan=False
    )  # Fail clearly before attempting to persist non-JSON output.
    with Session(engine) as session, session.begin():
        completed = session.execute(
            update(StepRun)
            .where(StepRun.id == step_run_id, StepRun.status == StepStatus.RUNNING)
            .values(
                status=StepStatus.COMPLETED,
                output=output,
                completed_at=datetime.now(timezone.utc),
            )
        )
        if completed.rowcount != 1:
            raise RuntimeError(f"Step {step_run_id} is no longer RUNNING")
    logger.info(json.dumps({"event": "step_completed", **log_fields}))
    return True
