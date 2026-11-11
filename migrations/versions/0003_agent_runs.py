"""Tenant-owned run registry, separate from LangGraph checkpoints."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0003_agent_runs"
down_revision = "0002_embeddings"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("user_id", sa.Text, nullable=False),
        sa.Column("owner_context", JSONB, nullable=False),
        sa.Column("input", JSONB, nullable=False),
        sa.Column("record", JSONB, nullable=False),
    )
    op.create_index("ix_agent_runs_owner", "agent_runs", ["tenant_id", "user_id"])


def downgrade():
    op.drop_table("agent_runs")
