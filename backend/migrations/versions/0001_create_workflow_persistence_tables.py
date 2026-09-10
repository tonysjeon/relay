"""Create workflow persistence tables"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column(
            "last_heartbeat",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_name", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                name="workflow_status",
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "step_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("step_name", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "READY",
                "RUNNING",
                "RETRYING",
                "COMPLETED",
                "FAILED",
                name="step_status",
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="1", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_step_attempt_count"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_step_max_attempts"),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_run_id", "id", name="uq_step_run_pair"),
        sa.UniqueConstraint(
            "workflow_run_id", "step_name", name="uq_step_name_per_run"
        ),
    )
    op.create_table(
        "step_dependencies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("step_run_id", sa.Uuid(), nullable=False),
        sa.Column("depends_on_step_run_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "step_run_id <> depends_on_step_run_id", name="ck_dependency_not_self"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id", "depends_on_step_run_id"],
            ["step_runs.workflow_run_id", "step_runs.id"],
            name="fk_dependency_prerequisite",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id", "step_run_id"],
            ["step_runs.workflow_run_id", "step_runs.id"],
            name="fk_dependency_step",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "step_run_id", "depends_on_step_run_id", name="uq_dependency_edge"
        ),
    )
    op.create_index(
        op.f("ix_step_dependencies_depends_on_step_run_id"),
        "step_dependencies",
        ["depends_on_step_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_step_dependencies_workflow_run_id"),
        "step_dependencies",
        ["workflow_run_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_step_dependencies_workflow_run_id"), table_name="step_dependencies"
    )
    op.drop_index(
        op.f("ix_step_dependencies_depends_on_step_run_id"),
        table_name="step_dependencies",
    )
    op.drop_table("step_dependencies")
    op.drop_table("step_runs")
    op.drop_table("workflow_runs")
    op.drop_table("workers")
    sa.Enum(name="step_status").drop(op.get_bind())
    sa.Enum(name="workflow_status").drop(op.get_bind())
