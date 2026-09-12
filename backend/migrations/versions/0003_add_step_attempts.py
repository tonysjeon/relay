"""Keep execution attempt history."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "step_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "step_run_id",
            sa.Uuid(),
            sa.ForeignKey("step_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint(
            "step_run_id", "attempt_number", name="uq_step_attempt_number"
        ),
        sa.CheckConstraint("attempt_number >= 1", name="ck_attempt_number"),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'FAILED', 'ABANDONED')",
            name="ck_attempt_status",
        ),
    )


def downgrade():
    op.drop_table("step_attempts")
