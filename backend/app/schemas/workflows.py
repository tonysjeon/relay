from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models import StepStatus, WorkflowStatus


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workflow_name: str
    status: WorkflowStatus
    input: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class AttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    attempt_number: int
    worker_id: str
    status: Literal["RUNNING", "COMPLETED", "FAILED", "ABANDONED"]
    started_at: datetime
    completed_at: datetime | None
    error: str | None


class StepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workflow_run_id: UUID
    step_name: str
    status: StepStatus
    input: dict[str, Any] | None
    output: Any
    error: str | None
    attempt_count: int
    max_attempts: int
    next_retry_at: datetime | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    depends_on: list[UUID] = []
    attempts: list[AttemptResponse] = []


class WorkflowDetailResponse(WorkflowResponse):
    steps: list[StepResponse]
