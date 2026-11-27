"""Add durable cancellation and execution deadlines."""

import sqlalchemy as sa
from alembic import op

revision = "0009_run_limits"
down_revision = "0008_run_worker"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_runs",
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("agent_runs", sa.Column("job_deadline", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("agent_runs", "job_deadline")
    op.drop_column("agent_runs", "cancel_requested")
