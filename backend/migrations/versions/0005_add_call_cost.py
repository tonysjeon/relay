"""Save token pricing and estimated call cost."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "llm_calls", sa.Column("cached_input_tokens", sa.Integer(), nullable=True)
    )
    op.add_column("llm_calls", sa.Column("pricing", postgresql.JSONB(), nullable=True))
    op.add_column(
        "llm_calls", sa.Column("estimated_cost_usd", sa.Numeric(24, 12), nullable=True)
    )
    op.create_check_constraint(
        "ck_llm_cached_tokens",
        "llm_calls",
        "cached_input_tokens >= 0 AND cached_input_tokens <= input_tokens",
    )
    op.create_check_constraint("ck_llm_cost", "llm_calls", "estimated_cost_usd >= 0")


def downgrade():
    op.drop_constraint("ck_llm_cost", "llm_calls")
    op.drop_constraint("ck_llm_cached_tokens", "llm_calls")
    op.drop_column("llm_calls", "estimated_cost_usd")
    op.drop_column("llm_calls", "pricing")
    op.drop_column("llm_calls", "cached_input_tokens")
