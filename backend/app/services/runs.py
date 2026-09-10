"""Persistence helpers. Callers own the session and commit or roll back the transaction."""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import StepDependency, StepRun, StepStatus, WorkflowRun


def create_workflow_run(
    session: Session, workflow_name: str, input: dict[str, Any]
) -> WorkflowRun:
    run = WorkflowRun(workflow_name=workflow_name, input=input)
    session.add(run)
    session.flush()
    return run


def create_step_runs(
    session: Session,
    workflow_run_id: UUID,
    step_names: Sequence[str],
    *,
    max_attempts: int = 1,
) -> list[StepRun]:
    steps = [
        StepRun(
            workflow_run_id=workflow_run_id, step_name=name, max_attempts=max_attempts
        )
        for name in step_names
    ]
    session.add_all(steps)
    session.flush()
    return steps


def create_step_dependencies(
    session: Session,
    workflow_run_id: UUID,
    dependencies: Mapping[str, Sequence[str]],
) -> list[StepDependency]:
    """Persist name-based edges; graph validation belongs to the workflow definition."""
    steps = {
        step.step_name: step.id for step in get_step_runs(session, workflow_run_id)
    }
    edges = []
    for name, prerequisites in dependencies.items():
        if name not in steps or any(parent not in steps for parent in prerequisites):
            raise ValueError("Dependencies must reference steps in this workflow run")
        edges.extend(
            StepDependency(
                workflow_run_id=workflow_run_id,
                step_run_id=steps[name],
                depends_on_step_run_id=steps[parent],
            )
            for parent in prerequisites
        )
    session.add_all(edges)
    session.flush()
    return edges


def get_workflow_run(session: Session, workflow_run_id: UUID) -> WorkflowRun | None:
    return session.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.id == workflow_run_id)
        .options(
            selectinload(WorkflowRun.steps), selectinload(WorkflowRun.dependencies)
        )
        .execution_options(populate_existing=True)
    )


def get_step_runs(session: Session, workflow_run_id: UUID) -> list[StepRun]:
    return list(
        session.scalars(
            select(StepRun)
            .where(StepRun.workflow_run_id == workflow_run_id)
            .order_by(StepRun.step_name)
        )
    )


def update_step_status(
    session: Session, step_run_id: UUID, status: StepStatus
) -> StepRun:
    """Store a status only; execution and transition rules are implemented in later phases."""
    step = session.get(StepRun, step_run_id)
    if step is None:
        raise LookupError(f"Step run {step_run_id} does not exist")
    step.status = StepStatus(status)
    session.flush()
    return step
