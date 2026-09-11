from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.models import WorkflowRun, WorkflowStatus
from app.schemas.workflows import (
    StepResponse,
    WorkflowDetailResponse,
    WorkflowResponse,
)
from app.services.runs import get_workflow_run


class WorkflowNotFound(LookupError):
    pass


class WorkflowConflict(ValueError):
    pass


def list_workflows(
    engine: Engine,
    *,
    limit: int = 50,
    offset: int = 0,
    status: WorkflowStatus | None = None,
) -> list[WorkflowResponse]:
    query = (
        select(WorkflowRun)
        .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        query = query.where(WorkflowRun.status == status)
    with Session(engine) as session:
        return [WorkflowResponse.model_validate(run) for run in session.scalars(query)]


def workflow_detail(engine: Engine, run_id: UUID) -> WorkflowDetailResponse:
    with Session(engine) as session:
        run = get_workflow_run(session, run_id)
        if run is None:
            raise WorkflowNotFound("Workflow run not found")
        parents = {step.id: [] for step in run.steps}
        for edge in run.dependencies:
            parents[edge.step_run_id].append(edge.depends_on_step_run_id)
        steps = [
            StepResponse.model_validate(step).model_copy(
                update={"depends_on": sorted(parents[step.id])}
            )
            for step in run.steps
        ]
        return WorkflowDetailResponse(
            **WorkflowResponse.model_validate(run).model_dump(), steps=steps
        )


def cancel_workflow(engine: Engine, run_id: UUID) -> WorkflowResponse:
    # Claims, completions, and schedulers lock this same row. Once cancellation
    # commits, no new claim can begin. Already-running handlers may still finish.
    with Session(engine) as session, session.begin():
        run = session.scalar(
            select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update()
        )
        if run is None:
            raise WorkflowNotFound("Workflow run not found")
        if run.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED):
            raise WorkflowConflict("A completed or failed workflow cannot be cancelled")
        if run.status != WorkflowStatus.CANCELLED:
            run.status = WorkflowStatus.CANCELLED
            run.completed_at = datetime.now(timezone.utc)
        return WorkflowResponse.model_validate(run)
