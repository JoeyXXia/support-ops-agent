"""Trusted operator import/query API. No HTTP authentication claim."""

from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from packages.storage.schema import document_versions as versions
from packages.storage.schema import documents


def scope_key(tenant_id):
    if tenant_id is None:
        return "public"
    if tenant_id not in {"tenant-alpha", "tenant-beta"}:
        raise ValueError("Unknown demo tenant")
    return "tenant:" + tenant_id


def import_batch(engine, items, *, tenant_id):
    """Atomic batch. Document locks are acquired in key order to avoid deadlocks."""
    scope = scope_key(tenant_id)
    keys = [item.document_key for item in items]
    if not keys or len(keys) != len(set(keys)):
        raise ValueError("Nonempty batch with unique document keys required")
    results = []
    with engine.begin() as conn:
        for item in sorted(items, key=lambda item: item.document_key):
            conn.execute(
                insert(documents)
                .values(
                    id=uuid4(),
                    scope_key=scope,
                    tenant_id=tenant_id,
                    document_key=item.document_key,
                )
                .on_conflict_do_nothing(constraint="uq_document_identity")
            )
            # Serialize revisions for a single document, including concurrent first imports.
            doc_id = conn.scalar(
                sa.select(documents.c.id)
                .where(
                    documents.c.scope_key == scope, documents.c.document_key == item.document_key
                )
                .with_for_update()
            )
            existing = (
                conn.execute(
                    sa.select(versions.c.id, versions.c.version_number).where(
                        versions.c.document_id == doc_id,
                        versions.c.revision_hash == item.revision_hash,
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                results.append(dict(document_key=item.document_key, created=False, **existing))
                continue
            number = (
                conn.scalar(
                    sa.select(sa.func.coalesce(sa.func.max(versions.c.version_number), 0)).where(
                        versions.c.document_id == doc_id
                    )
                )
                + 1
            )
            version_id = uuid4()
            conn.execute(
                versions.insert().values(
                    id=version_id,
                    document_id=doc_id,
                    version_number=number,
                    revision_hash=item.revision_hash,
                    content_hash=item.content_hash,
                    body=item.body,
                    title=item.title,
                    source_path=item.source_path,
                    data_version=item.data_version,
                    product_versions=list(item.product_versions),
                    synthetic=item.synthetic,
                )
            )
            results.append(
                dict(
                    document_key=item.document_key,
                    created=True,
                    id=version_id,
                    version_number=number,
                )
            )
    return results


def query_versions(engine, *, tenant_id, document_key=None):
    # Caller supplies trusted scope; public rules are visible alongside that tenant's data.
    allowed = ["public"] if tenant_id is None else ["public", scope_key(tenant_id)]
    query = (
        sa.select(
            documents.c.document_key,
            documents.c.scope_key,
            documents.c.tenant_id,
            *versions.c,
        )
        .join(versions)
        .where(documents.c.scope_key.in_(allowed))
    )
    if document_key is not None:
        query = query.where(documents.c.document_key == document_key)
    with engine.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                query.order_by(
                    documents.c.scope_key, documents.c.document_key, versions.c.version_number
                )
            ).mappings()
        ]
