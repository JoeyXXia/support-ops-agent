"""Positioned chunks and scope-isolated embedding cache. No retrieval indexes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import UserDefinedType

revision = "0002_embeddings"
down_revision = "0001_documents"
branch_labels = None
depends_on = None


class FrozenVector1536(UserDefinedType):
    def get_col_spec(self, **kw):
        return "VECTOR(1536)"


def upgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM chunks")):
        raise RuntimeError("Day 8 placeholder chunks must be empty before migration")
    op.create_table(
        "embedding_cache",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scope_key", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("dimensions", sa.Integer, nullable=False),
        sa.Column("input_version", sa.Text, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("embedding", FrozenVector1536(), nullable=False),
        sa.CheckConstraint("dimensions = 1536", name="ck_embedding_dimensions"),
        sa.UniqueConstraint(
            "scope_key",
            "provider",
            "model",
            "dimensions",
            "input_version",
            "input_hash",
            name="uq_embedding_identity",
        ),
    )
    for column in (
        sa.Column("start_char", sa.Integer, nullable=False),
        sa.Column("end_char", sa.Integer, nullable=False),
        sa.Column("start_line", sa.Integer, nullable=False),
        sa.Column("end_line", sa.Integer, nullable=False),
        sa.Column("heading_path", JSONB, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("policy", sa.Text, nullable=False),
        sa.Column("embedding_id", UUID(as_uuid=True), nullable=False),
    ):
        op.add_column("chunks", column)
    op.create_foreign_key(
        "fk_chunk_embedding", "chunks", "embedding_cache", ["embedding_id"], ["id"]
    )
    op.create_check_constraint(
        "ck_chunk_position",
        "chunks",
        "start_char >= 0 AND end_char > start_char AND start_line >= 1 AND end_line >= start_line",
    )
    op.add_column(
        "document_versions",
        sa.Column("embedding_status", sa.Text, nullable=False, server_default="pending"),
    )
    op.add_column("document_versions", sa.Column("embedding_profile", sa.Text))
    op.add_column("document_versions", sa.Column("embedding_error", sa.Text))
    op.create_check_constraint(
        "ck_embedding_status",
        "document_versions",
        "embedding_status IN ('pending','processing','ready','failed')",
    )


def downgrade():
    op.drop_constraint("ck_embedding_status", "document_versions", type_="check")
    for name in ("embedding_error", "embedding_profile", "embedding_status"):
        op.drop_column("document_versions", name)
    op.drop_constraint("ck_chunk_position", "chunks", type_="check")
    op.drop_constraint("fk_chunk_embedding", "chunks", type_="foreignkey")
    for name in (
        "start_char",
        "end_char",
        "start_line",
        "end_line",
        "heading_path",
        "content_hash",
        "input_hash",
        "policy",
        "embedding_id",
    ):
        op.drop_column("chunks", name)
    op.drop_table("embedding_cache")
