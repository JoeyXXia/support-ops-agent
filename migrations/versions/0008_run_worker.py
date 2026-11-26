"""Add durable worker state to run rows."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0008_run_worker"
down_revision = "0007_user_preferences"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_runs",
        sa.Column("job_pending", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "agent_runs", sa.Column("job_attempts", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("agent_runs", sa.Column("job_lease_id", UUID(), nullable=True))
    op.add_column(
        "agent_runs", sa.Column("job_lease_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_agent_runs_pending",
        "agent_runs",
        ["job_lease_until", "id"],
        postgresql_where=sa.text("job_pending"),
    )


def downgrade():
    op.drop_index("ix_agent_runs_pending", table_name="agent_runs")
    for name in ("job_lease_until", "job_lease_id", "job_attempts", "job_pending"):
        op.drop_column("agent_runs", name)
