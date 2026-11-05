"""Versioned Markdown storage. No vectors or chunk generation yet."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001_documents"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Do not import mutable application metadata into historical migrations.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    op.create_table(
        "documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scope_key", sa.Text, nullable=False),
        sa.Column("tenant_id", sa.Text),
        sa.Column("document_key", sa.Text, nullable=False),
        sa.UniqueConstraint("scope_key", "document_key", name="uq_document_identity"),
        sa.CheckConstraint(
            "(scope_key = 'public' AND tenant_id IS NULL) OR "
            "(tenant_id IS NOT NULL AND tenant_id <> '' AND scope_key = 'tenant:' || tenant_id)",
            name="ck_document_scope",
        ),
    )
    op.create_table(
        "document_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("source_path", sa.Text, nullable=False),
        sa.Column("data_version", sa.Text, nullable=False),
        sa.Column("product_versions", JSONB, nullable=False),
        sa.Column("synthetic", sa.Boolean, nullable=False),
        sa.Column(
            "imported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("document_id", "revision_hash", name="uq_document_revision"),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
        sa.CheckConstraint("version_number > 0", name="ck_version_positive"),
    )
    op.create_table(
        "chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id", UUID(as_uuid=True), sa.ForeignKey("document_versions.id"), nullable=False
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.UniqueConstraint("version_id", "ordinal", name="uq_chunk_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_chunk_ordinal"),
    )


def downgrade():
    op.drop_table("chunks")
    op.drop_table("document_versions")
    op.drop_table("documents")
    # Extension may be shared by other tables; never drop it implicitly.
