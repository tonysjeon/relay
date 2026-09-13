"""Persist approval requests and decisions."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE step_status ADD VALUE IF NOT EXISTS 'WAITING_APPROVAL'")
    op.add_column(
        "step_runs",
        sa.Column(
            "requires_approval", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "step_runs", sa.Column("approval_decision", sa.String(), nullable=True)
    )
    op.add_column("step_runs", sa.Column("approval_note", sa.Text(), nullable=True))
    op.add_column(
        "step_runs",
        sa.Column("approval_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "step_runs",
        sa.Column("approval_decided_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    # Refuse to discard pending reviews or recorded decisions.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM step_runs WHERE requires_approval) THEN
                RAISE EXCEPTION 'Cannot downgrade while approval steps exist';
            END IF;
        END $$;
    """)
    for column in (
        "approval_decided_at",
        "approval_requested_at",
        "approval_note",
        "approval_decision",
        "requires_approval",
    ):
        op.drop_column("step_runs", column)
    op.execute("ALTER TABLE step_runs ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE step_status RENAME TO step_status_old")
    op.execute(
        "CREATE TYPE step_status AS ENUM ('PENDING', 'READY', 'RUNNING', 'RETRYING', 'COMPLETED', 'FAILED')"
    )
    op.execute(
        "ALTER TABLE step_runs ALTER COLUMN status TYPE step_status USING status::text::step_status"
    )
    op.execute(
        "ALTER TABLE step_runs ALTER COLUMN status SET DEFAULT 'PENDING'::step_status"
    )
    op.execute("DROP TYPE step_status_old")
