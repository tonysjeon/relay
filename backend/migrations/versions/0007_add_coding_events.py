"""Record externally observed coding sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "coding_events",
        sa.Column("sequence", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("session_id", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("cwd", sa.String(2000), nullable=False),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("turn_id", sa.String(200), nullable=True),
        sa.Column("tool_name", sa.String(200), nullable=True),
        sa.Column("tool_use_id", sa.String(200), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_coding_session_sequence", "coding_events", ["session_id", "sequence"]
    )


def downgrade():
    op.drop_table("coding_events")
