from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class CodingEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    session_id: str = Field(min_length=1, max_length=200)
    event_type: Literal[
        "SessionStart",
        "SessionEnd",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Stop",
        "Interrupt",
    ]
    occurred_at: AwareDatetime
    cwd: str = Field(max_length=2000)
    model: str | None = Field(default=None, max_length=200)
    turn_id: str | None = Field(default=None, max_length=200)
    tool_name: str | None = Field(default=None, max_length=200)
    tool_use_id: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=8000)


class CodingEventResponse(CodingEventInput):
    model_config = ConfigDict(from_attributes=True)
    sequence: int
    received_at: AwareDatetime


class CodingSessionResponse(BaseModel):
    session_id: str
    cwd: str
    model: str | None
    event_count: int
    last_event: str
    last_seen: AwareDatetime
