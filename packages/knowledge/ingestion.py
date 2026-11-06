"""Cache batches durably; atomically publish a complete version. One writer per version."""

import hashlib
from dataclasses import asdict
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from packages.storage.schema import chunks, document_versions, documents, embedding_cache

from .chunking import INPUT_VERSION, POLICY, split_markdown
from .embeddings import BATCH_SIZE, DIMENSIONS, EmbeddingError, validate_vectors
from .repository import scope_key


def ingest_version(engine, version_id, provider, *, tenant_id=None, batch_size=BATCH_SIZE):
    if type(batch_size) is not int or not 1 <= batch_size <= BATCH_SIZE:
        raise ValueError("batch_size must be 1..16")
    allowed = ["public"] if tenant_id is None else ["public", scope_key(tenant_id)]
    with engine.connect() as conn:
        row = (
            conn.execute(
                sa.select(document_versions, documents.c.scope_key)
                .join(documents)
                .where(document_versions.c.id == version_id, documents.c.scope_key.in_(allowed))
            )
            .mappings()
            .first()
        )
    if row is None:
        raise ValueError("version_not_found_or_not_authorized")
    profile = f"{POLICY}|{provider.provider}|{provider.model}|{DIMENSIONS}|{INPUT_VERSION}"
    result = dict(
        version_id=str(version_id),
        provider=provider.provider,
        model=provider.model,
        dimensions=DIMENSIONS,
        batches=0,
        attempts=0,
        cache_hits=0,
        chunks=0,
        request_ids=[],
        usage_tokens=None,
        status="processing",
    )
    # A session advisory lock survives short commits but is released if its connection dies.
    lock_id = int.from_bytes(hashlib.sha256(str(version_id).encode()).digest()[:8], "big") % (2**63)
    with engine.connect() as guard:
        acquired = guard.scalar(sa.text("SELECT pg_try_advisory_lock(:key)"), dict(key=lock_id))
        guard.commit()
        if not acquired:
            raise ValueError("version_busy")
        try:
            with engine.begin() as conn:
                conn.execute(
                    document_versions.update()
                    .where(document_versions.c.id == version_id)
                    .values(
                        embedding_status="processing",
                        embedding_profile=profile,
                        embedding_error=None,
                    )
                )
            pieces = split_markdown(row["body"])
            result["chunks"] = len(pieces)
            ids = {}

            def condition(input_hash):
                return sa.and_(
                    embedding_cache.c.scope_key == row["scope_key"],
                    embedding_cache.c.provider == provider.provider,
                    embedding_cache.c.model == provider.model,
                    embedding_cache.c.dimensions == DIMENSIONS,
                    embedding_cache.c.input_version == INPUT_VERSION,
                    embedding_cache.c.input_hash == input_hash,
                )

            unique = {piece.input_hash: piece for piece in pieces}
            with engine.connect() as conn:
                for key in unique:
                    cached = conn.scalar(sa.select(embedding_cache.c.id).where(condition(key)))
                    if cached:
                        ids[key] = cached
                        result["cache_hits"] += 1
            missing = [piece for key, piece in unique.items() if key not in ids]
            for start in range(0, len(missing), batch_size):
                batch = missing[start : start + batch_size]
                output = provider.embed([piece.embedding_input for piece in batch])
                validate_vectors(output.vectors, len(batch))
                result["batches"] += 1
                result["attempts"] += output.attempts
                if output.request_id:
                    result["request_ids"].append(output.request_id)
                if output.usage_tokens is not None:
                    result["usage_tokens"] = (result["usage_tokens"] or 0) + output.usage_tokens
                with engine.begin() as conn:
                    for piece, vector in zip(batch, output.vectors, strict=True):
                        conn.execute(
                            insert(embedding_cache)
                            .values(
                                id=uuid4(),
                                scope_key=row["scope_key"],
                                provider=provider.provider,
                                model=provider.model,
                                dimensions=DIMENSIONS,
                                input_version=INPUT_VERSION,
                                input_hash=piece.input_hash,
                                embedding=vector,
                            )
                            .on_conflict_do_nothing(constraint="uq_embedding_identity")
                        )
                        ids[piece.input_hash] = conn.scalar(
                            sa.select(embedding_cache.c.id).where(condition(piece.input_hash))
                        )
            # No chunks become queryable as a new ready profile before this complete transaction.
            with engine.begin() as conn:
                conn.execute(
                    chunks.delete().where(
                        chunks.c.version_id == version_id, chunks.c.ordinal >= len(pieces)
                    )
                )
                for piece in pieces:
                    values = asdict(piece)
                    values.pop("embedding_input")
                    values.update(
                        version_id=version_id, policy=POLICY, embedding_id=ids[piece.input_hash]
                    )
                    statement = insert(chunks).values(id=uuid4(), **values)
                    conn.execute(
                        statement.on_conflict_do_update(constraint="uq_chunk_ordinal", set_=values)
                    )
                conn.execute(
                    document_versions.update()
                    .where(document_versions.c.id == version_id)
                    .values(
                        embedding_status="ready", embedding_error=None, embedding_profile=profile
                    )
                )
            result["status"] = "ready"
        except Exception as exc:
            code = exc.code if isinstance(exc, EmbeddingError) else "processing_error"
            with engine.begin() as conn:
                conn.execute(
                    document_versions.update()
                    .where(document_versions.c.id == version_id)
                    .values(embedding_status="failed", embedding_error=code)
                )
            result.update(status="failed", error=code)
            result["attempts"] += getattr(exc, "attempts", 0)
            if isinstance(exc, EmbeddingError) and exc.request_id:
                result["request_ids"].append(exc.request_id)
        finally:
            guard.execute(sa.text("SELECT pg_advisory_unlock(:key)"), dict(key=lock_id))
            guard.commit()
    return result
