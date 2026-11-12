"""Bind new approvals to a payload digest, actor and expiration."""

import sqlalchemy as sa
from alembic import op

revision = "0005_approval_binding"
down_revision = "0004_tickets"
branch_labels = None
depends_on = None


def upgrade():
    # Historical drafts remain readable. Missing bindings fail closed at execution.
    op.add_column("approvals", sa.Column("payload_digest", sa.Text))
    op.add_column("approvals", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column("approvals", sa.Column("decided_by", sa.Text))
    op.add_column("approvals", sa.Column("decided_at", sa.DateTime(timezone=True)))


def downgrade():
    for name in ("decided_at", "decided_by", "expires_at", "payload_digest"):
        op.drop_column("approvals", name)
