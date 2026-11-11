"""Core table definitions; migrations own DDL, never application startup."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .vector import Vector1536

metadata = sa.MetaData()
documents = sa.Table(
    "documents",
    metadata,
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
document_versions = sa.Table(
    "document_versions",
    metadata,
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
chunks = sa.Table(
    "chunks",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "version_id", UUID(as_uuid=True), sa.ForeignKey("document_versions.id"), nullable=False
    ),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.UniqueConstraint("version_id", "ordinal", name="uq_chunk_ordinal"),
    sa.CheckConstraint("ordinal >= 0", name="ck_chunk_ordinal"),
)


embedding_cache = sa.Table(
    "embedding_cache",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("scope_key", sa.Text, nullable=False),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("dimensions", sa.Integer, nullable=False),
    sa.Column("input_version", sa.Text, nullable=False),
    sa.Column("input_hash", sa.String(64), nullable=False),
    sa.Column("embedding", Vector1536(), nullable=False),
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
    sa.Column(
        "embedding_id", UUID(as_uuid=True), sa.ForeignKey("embedding_cache.id"), nullable=False
    ),
):
    chunks.append_column(column)
chunks.append_constraint(
    sa.CheckConstraint(
        "start_char >= 0 AND end_char > start_char AND start_line >= 1 AND end_line >= start_line",
        name="ck_chunk_position",
    )
)
for column in (
    sa.Column("embedding_status", sa.Text, nullable=False, server_default="pending"),
    sa.Column("embedding_profile", sa.Text),
    sa.Column("embedding_error", sa.Text),
):
    document_versions.append_column(column)
document_versions.append_constraint(
    sa.CheckConstraint(
        "embedding_status IN ('pending','processing','ready','failed')", name="ck_embedding_status"
    )
)


agent_runs = sa.Table(
    "agent_runs",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", sa.Text, nullable=False),
    sa.Column("user_id", sa.Text, nullable=False),
    sa.Column("owner_context", JSONB, nullable=False),
    sa.Column("input", JSONB, nullable=False),
    sa.Column("record", JSONB, nullable=False),
    sa.Index("ix_agent_runs_owner", "tenant_id", "user_id"),
)
