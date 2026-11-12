"""Tickets and immutable proposals, awaiting an approval workflow."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004_tickets"
down_revision = "0003_agent_runs"
branch_labels = None
depends_on = None
frozen = sa.MetaData()
sa.Table("agent_runs", frozen, sa.Column("id", UUID(as_uuid=True), primary_key=True))

tickets = sa.Table(
    "tickets",
    frozen,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", sa.Text, nullable=False),
    sa.Column("workspace_id", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint("status IN ('open','closed')", name="ck_ticket_status"),
    sa.CheckConstraint("length(btrim(title)) BETWEEN 1 AND 120", name="ck_ticket_title"),
    sa.CheckConstraint(
        "length(btrim(description)) BETWEEN 1 AND 2000", name="ck_ticket_description"
    ),
    sa.Index("ix_tickets_tenant", "tenant_id"),
)
approvals = sa.Table(
    "approvals",
    frozen,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=False),
    sa.Column("tenant_id", sa.Text, nullable=False),
    sa.Column("requested_by", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("params", JSONB, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("ticket_id", UUID(as_uuid=True), sa.ForeignKey("tickets.id")),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint("action = 'create_ticket'", name="ck_approval_action"),
    sa.CheckConstraint(
        "status IN ('pending','approved','rejected','executed')", name="ck_approval_status"
    ),
    sa.CheckConstraint(
        "(status = 'executed') = (ticket_id IS NOT NULL)", name="ck_approval_execution"
    ),
    sa.UniqueConstraint("run_id", "action", name="uq_run_action"),
)


def upgrade():
    tickets.create(op.get_bind())
    approvals.create(op.get_bind())


def downgrade():
    approvals.drop(op.get_bind())
    tickets.drop(op.get_bind())
