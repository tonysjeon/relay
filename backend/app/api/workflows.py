from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.models import WorkflowStatus
from app.schemas.workflows import (
    ApprovalRequest,
    StepResponse,
    WorkflowDetailResponse,
    WorkflowResponse,
)
from app.services.approvals import decide_approval
from app.services.workflow_api import (
    WorkflowConflict,
    WorkflowNotFound,
    cancel_workflow,
    count_workflows,
    list_workflows,
    workflow_detail,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("", response_model=list[WorkflowResponse])
def workflows(
    request: Request,
    response: Response,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: WorkflowStatus | None = None,
):
    response.headers["X-Total-Count"] = str(
        count_workflows(request.app.state.engine, status=status)
    )
    return list_workflows(
        request.app.state.engine, limit=limit, offset=offset, status=status
    )


@router.get("/{run_id}", response_model=WorkflowDetailResponse)
def workflow(request: Request, run_id: UUID):
    try:
        return workflow_detail(request.app.state.engine, run_id)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{run_id}/steps", response_model=list[StepResponse])
def steps(request: Request, run_id: UUID):
    return workflow(request, run_id).steps


@router.post("/{run_id}/cancel", response_model=WorkflowResponse)
def cancel(request: Request, run_id: UUID):
    try:
        return cancel_workflow(request.app.state.engine, run_id)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{run_id}/steps/{step_id}/approval", response_model=WorkflowDetailResponse
)
def approval(request: Request, run_id: UUID, step_id: UUID, body: ApprovalRequest):
    try:
        decide_approval(
            request.app.state.engine, run_id, step_id, body.decision, body.note
        )
        return workflow_detail(request.app.state.engine, run_id)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
