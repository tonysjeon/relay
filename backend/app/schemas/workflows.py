from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import StepStatus, WorkflowStatus


class UsageResponse(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    missing_token_calls: int = 0
    estimated_cost_usd: Decimal = Decimal(0)
    unpriced_calls: int = 0


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    usage: UsageResponse = UsageResponse()
    workflow_name: str
    status: WorkflowStatus
    input: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class LLMCallResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cached_input_tokens: int | None
    pricing: dict | None
    estimated_cost_usd: Decimal | None
    provider: str
    model: str
    input: Any
    output: Any
    input_tokens: int | None
    output_tokens: int | None
    status: Literal["RUNNING", "COMPLETED", "FAILED", "ABANDONED"]
    error: str | None
    started_at: datetime
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

    llm_calls: list[LLMCallResponse] = []


class ApprovalRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=2000)


class StepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workflow_run_id: UUID
    requires_approval: bool
    approval_decision: str | None
    approval_note: str | None
    approval_requested_at: datetime | None
    approval_decided_at: datetime | None
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
