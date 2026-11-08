"""Development-set evidence hit rate over a repeatable database snapshot."""

from hashlib import sha256

import sqlalchemy as sa

from evals.schema import EvalCase
from packages.knowledge.chunking import INPUT_VERSION, POLICY
from packages.knowledge.embeddings import DIMENSIONS
from packages.knowledge.repository import scope_key
from packages.knowledge.retrieval import search_chunks
from packages.storage.schema import document_versions, documents


def evaluate(connection, cases_path, provider):
    raw = cases_path.read_bytes()
    cases = [EvalCase.model_validate_json(line) for line in raw.splitlines() if line.strip()]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("duplicate_eval_case_id")
    rows = []
    for case in cases:
        row = dict(
            id=case.id,
            tenant_id=case.tenant_id,
            product_version=case.product_version,
            answerability=case.answerability,
            expected_sources=case.relevant_sources,
        )
        if not case.product_version or not case.relevant_sources:
            row["excluded"] = (
                "missing_product_version" if not case.product_version else "no_relevant_sources"
            )
        else:
            vector = provider.embed([case.question]).vectors[0]
            results = search_chunks(
                connection,
                vector,
                tenant_id=case.tenant_id,
                product_version=case.product_version,
                provider=provider.provider,
                model=provider.model,
            )
            # Day 2 labels refer to public fixtures, not same-named tenant documents.
            row["hit"] = any(
                hit["scope_key"] == "public" and hit["source"] in case.relevant_sources
                for hit in results
            )
            row["top5"] = [
                {key: val for key, val in hit.items() if key != "body"} for hit in results
            ]
        rows.append(row)
    scored = [row for row in rows if "hit" in row]
    hits = sum(row["hit"] for row in scored)
    scopes = {"public", *(scope_key(case.tenant_id) for case in cases)}
    revisions = connection.execute(
        sa.select(
            documents.c.scope_key,
            documents.c.document_key,
            document_versions.c.version_number,
            document_versions.c.revision_hash,
            document_versions.c.data_version,
            document_versions.c.product_versions,
            document_versions.c.embedding_status,
            document_versions.c.embedding_profile,
        )
        .join(documents, document_versions.c.document_id == documents.c.id)
        .where(documents.c.scope_key.in_(scopes))
        .order_by(
            documents.c.scope_key, documents.c.document_key, document_versions.c.version_number
        )
    ).mappings()
    return dict(
        dataset_sha256=sha256(raw).hexdigest(),
        provider=provider.provider,
        model=provider.model,
        dimensions=DIMENSIONS,
        chunk_policy=POLICY,
        embedding_input_version=INPUT_VERSION,
        k=5,
        hits=hits,
        scored=len(scored),
        hit_at_5=hits / len(scored) if scored else None,
        missed_ids=[row["id"] for row in scored if not row["hit"]],
        corpus_revisions=[dict(row) for row in revisions],
        cases=rows,
    )
