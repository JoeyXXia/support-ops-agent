"""Scoped exact pgvector search for trusted operator callers."""

import sqlalchemy as sa

from packages.storage.schema import chunks, document_versions, documents, embedding_cache
from packages.storage.vector import Vector1536

from .chunking import INPUT_VERSION, POLICY
from .embeddings import DIMENSIONS, validate_vectors
from .repository import scope_key


def search_chunks(connection, vector, *, tenant_id, product_version, provider, model, k=5):
    if not isinstance(product_version, str) or not product_version.strip():
        raise ValueError("product_version_required")
    if type(k) is not int or not 1 <= k <= 50:
        raise ValueError("k_must_be_1_to_50")
    validate_vectors([vector], 1)
    scopes = ["public", scope_key(tenant_id)] if tenant_id is not None else ["public"]
    profile = f"{POLICY}|{provider}|{model}|{DIMENSIONS}|{INPUT_VERSION}"
    # Choose the latest applicable revision even if it is unready. Never fall back silently.
    ranked = (
        sa.select(
            document_versions.c.id,
            sa.func.row_number()
            .over(
                partition_by=document_versions.c.document_id,
                order_by=document_versions.c.version_number.desc(),
            )
            .label("revision_rank"),
        )
        .join(documents, document_versions.c.document_id == documents.c.id)
        .where(
            documents.c.scope_key.in_(scopes),
            document_versions.c.product_versions.contains([product_version.strip()]),
        )
        .subquery()
    )
    query_vector = sa.cast(sa.literal(vector, type_=Vector1536()), Vector1536())
    distance = embedding_cache.c.embedding.op("<=>", return_type=sa.Float())(query_vector)
    stmt = (
        sa.select(
            chunks.c.id.label("chunk_id"),
            chunks.c.body,
            chunks.c.ordinal,
            chunks.c.heading_path,
            chunks.c.start_char,
            chunks.c.end_char,
            chunks.c.start_line,
            chunks.c.end_line,
            documents.c.scope_key,
            documents.c.document_key,
            document_versions.c.source_path,
            document_versions.c.id.label("version_id"),
            document_versions.c.version_number,
            document_versions.c.revision_hash,
            document_versions.c.data_version,
            (1.0 - distance).label("score"),
        )
        .select_from(ranked)
        .join(document_versions, document_versions.c.id == ranked.c.id)
        .join(documents, documents.c.id == document_versions.c.document_id)
        .join(chunks, chunks.c.version_id == document_versions.c.id)
        .join(embedding_cache, embedding_cache.c.id == chunks.c.embedding_id)
        .where(
            ranked.c.revision_rank == 1,
            document_versions.c.embedding_status == "ready",
            document_versions.c.embedding_profile == profile,
            chunks.c.policy == POLICY,
            embedding_cache.c.scope_key == documents.c.scope_key,
            embedding_cache.c.provider == provider,
            embedding_cache.c.model == model,
            embedding_cache.c.dimensions == DIMENSIONS,
            embedding_cache.c.input_version == INPUT_VERSION,
            embedding_cache.c.input_hash == chunks.c.input_hash,
        )
        .order_by(distance, documents.c.scope_key, documents.c.document_key, chunks.c.ordinal)
        .limit(k)
    )
    results = []
    for row in connection.execute(stmt).mappings():
        item = dict(row)
        headings = row["heading_path"]
        item["source"] = (
            f"{row['document_key']}#{headings[-1]}" if len(headings) > 1 else row["document_key"]
        )
        results.append(item)
    return results
