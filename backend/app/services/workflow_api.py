from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Engine, func, or_, select
from sqlalchemy.orm import Session

from app.models import LLMCall, StepAttempt, StepRun, WorkflowRun, WorkflowStatus
from app.schemas.workflows import (
    StepResponse,
    UsageResponse,
    WorkflowDetailResponse,
    WorkflowResponse,
)
from app.services.runs import get_workflow_run


class WorkflowNotFound(LookupError):
    pass


class WorkflowConflict(ValueError):
    pass


def usage_totals(session, run_ids):
    if not run_ids:
        return {}
    query = (
        select(
            StepRun.workflow_run_id,
            func.count(LLMCall.id).label("calls"),
            func.coalesce(func.sum(LLMCall.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(LLMCall.output_tokens), 0).label("output_tokens"),
            func.count(LLMCall.id)
            .filter(
                or_(LLMCall.input_tokens.is_(None), LLMCall.output_tokens.is_(None))
            )
            .label("missing_token_calls"),
            func.coalesce(func.sum(LLMCall.estimated_cost_usd), 0).label(
                "estimated_cost_usd"
            ),
            func.count(LLMCall.id)
            .filter(LLMCall.estimated_cost_usd.is_(None))
            .label("unpriced_calls"),
        )
        .select_from(StepRun)
        .join(StepAttempt, StepAttempt.step_run_id == StepRun.id)
        .join(LLMCall, LLMCall.step_attempt_id == StepAttempt.id)
        .where(StepRun.workflow_run_id.in_(run_ids))
        .group_by(StepRun.workflow_run_id)
    )
    return {
        row.workflow_run_id: UsageResponse(**row._mapping)
        for row in session.execute(query)
    }


def run_response(session, run):
    return WorkflowResponse.model_validate(run).model_copy(
        update={"usage": usage_totals(session, [run.id]).get(run.id, UsageResponse())}
    )


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
        runs = list(session.scalars(query))
        totals = usage_totals(session, [run.id for run in runs])
        return [
            WorkflowResponse.model_validate(run).model_copy(
                update={"usage": totals.get(run.id, UsageResponse())}
            )
            for run in runs
        ]


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
            **run_response(session, run).model_dump(), steps=steps
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
        return run_response(session, run)
