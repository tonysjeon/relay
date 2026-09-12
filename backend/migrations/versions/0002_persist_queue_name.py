"""Persist workflow queue routing for recovery."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "workflow_runs",
        sa.Column(
            "queue_name", sa.String(), nullable=False, server_default="relay:jobs"
        ),
    )


def downgrade():
    op.drop_column("workflow_runs", "queue_name")
