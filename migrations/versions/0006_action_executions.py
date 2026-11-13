"""Persist one result per approved logical action, including historical results."""

import hashlib
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0006_action_executions"
down_revision = "0005_approval_binding"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "action_executions",
        sa.Column(
            "approval_id", UUID(as_uuid=True), sa.ForeignKey("approvals.id"), primary_key=True
        ),
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("payload_digest", sa.Text, nullable=False),
        sa.Column(
            "ticket_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tickets.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("result", JSONB, nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_execution_action_key"),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("""SELECT a.id, a.run_id, a.tenant_id, a.requested_by,
        a.action, a.params, t.id AS ticket_id, t.workspace_id, t.title, t.description, t.status
        FROM approvals a JOIN tickets t ON t.id=a.ticket_id AND t.tenant_id=a.tenant_id
        WHERE a.status='executed'""")
    ).mappings()
    for row in rows:
        payload = {name: row[name] for name in ("tenant_id", "requested_by", "action", "params")}
        payload["run_id"] = str(row["run_id"])
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        result = {
            name: row[name]
            for name in ("tenant_id", "workspace_id", "title", "description", "status")
        }
        result["ticket_id"] = str(row["ticket_id"])
        connection.execute(
            sa.text("""INSERT INTO action_executions
            (approval_id,tenant_id,idempotency_key,payload_digest,ticket_id,result)
            VALUES (:id,:tenant,:key,:digest,:ticket,CAST(:result AS jsonb))"""),
            {
                "id": row["id"],
                "tenant": row["tenant_id"],
                "key": "create_ticket:" + str(row["id"]),
                "digest": digest,
                "ticket": row["ticket_id"],
                "result": json.dumps(result, ensure_ascii=False),
            },
        )


def downgrade():
    op.drop_table("action_executions")
