from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class WorkflowStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workflow_name: Mapped[str] = mapped_column(String, nullable=False)
    queue_name: Mapped[str] = mapped_column(String, server_default="relay:jobs")
    status: Mapped[WorkflowStatus] = mapped_column(
        Enum(WorkflowStatus, name="workflow_status", validate_strings=True),
        server_default="PENDING",
    )
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list["StepRun"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="StepRun.step_name",
    )
    dependencies: Mapped[list["StepDependency"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class StepRun(Base):
    __tablename__ = "step_runs"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "step_name", name="uq_step_name_per_run"),
        UniqueConstraint("workflow_run_id", "id", name="uq_step_run_pair"),
        CheckConstraint("attempt_count >= 0", name="ck_step_attempt_count"),
        CheckConstraint("max_attempts >= 1", name="ck_step_max_attempts"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workflow_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    step_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[StepStatus] = mapped_column(
        Enum(StepStatus, name="step_status", validate_strings=True),
        server_default="PENDING",
    )
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[Any | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="1")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attempts: Mapped[list["StepAttempt"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="StepAttempt.attempt_number",
    )


class StepAttempt(Base):
    __tablename__ = "step_attempts"
    __table_args__ = (
        UniqueConstraint(
            "step_run_id", "attempt_number", name="uq_step_attempt_number"
        ),
        CheckConstraint("attempt_number >= 1", name="ck_attempt_number"),
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'FAILED', 'ABANDONED')",
            name="ck_attempt_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    step_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("step_runs.id", ondelete="CASCADE")
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class StepDependency(Base):
    __tablename__ = "step_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_run_id", "step_run_id"],
            ["step_runs.workflow_run_id", "step_runs.id"],
            ondelete="CASCADE",
            name="fk_dependency_step",
        ),
        ForeignKeyConstraint(
            ["workflow_run_id", "depends_on_step_run_id"],
            ["step_runs.workflow_run_id", "step_runs.id"],
            ondelete="CASCADE",
            name="fk_dependency_prerequisite",
        ),
        UniqueConstraint(
            "step_run_id", "depends_on_step_run_id", name="uq_dependency_edge"
        ),
        CheckConstraint(
            "step_run_id <> depends_on_step_run_id", name="ck_dependency_not_self"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workflow_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    step_run_id: Mapped[UUID] = mapped_column(nullable=False)
    depends_on_step_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    last_heartbeat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
