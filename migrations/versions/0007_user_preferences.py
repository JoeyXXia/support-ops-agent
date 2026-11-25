"""Store explicitly confirmed product version defaults."""

import sqlalchemy as sa
from alembic import op

revision = "0007_user_preferences"
down_revision = "0006_action_executions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_preferences",
        sa.Column("tenant_id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("product_version", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "product_version IN ('1.0','2.0')", name="ck_preference_product_version"
        ),
    )


def downgrade():
    op.drop_table("user_preferences")
