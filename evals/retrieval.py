"""Development-set evidence hit rate over a repeatable database snapshot."""

from hashlib import sha256
from statistics import median
from time import perf_counter_ns

import sqlalchemy as sa

from evals.schema import EvalCase
from packages.knowledge.chunking import INPUT_VERSION, POLICY
from packages.knowledge.context import CANDIDATE_DEPTH, TOKEN_BUDGET, build_context
from packages.knowledge.embeddings import DIMENSIONS
from packages.knowledge.repository import scope_key
from packages.knowledge.retrieval import search_query
from packages.storage.schema import document_versions, documents


def evaluate(connection, cases_path, provider, *, mode="vector", context_rerank=None):
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
            candidate_options = {} if context_rerank is None else {"k": CANDIDATE_DEPTH}
            results = search_query(
                connection,
                case.question,
                provider,
                tenant_id=case.tenant_id,
                product_version=case.product_version,
                mode=mode,
                **candidate_options,
            )
            if context_rerank is not None:
                row["candidate_ids"] = [str(item["chunk_id"]) for item in results]
                started = perf_counter_ns()
                context = build_context(case.question, results, rerank=context_rerank)
                row["context_ms"] = (perf_counter_ns() - started) / 1_000_000
                row["context_utf8_bytes"] = context["utf8_bytes"]
                row["rerank_status"] = context["rerank_status"]
                results = context["sources"]
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


def compare_context(connection, cases_path, provider):
    runs = {
        name: evaluate(connection, cases_path, provider, mode="hybrid", context_rerank=enabled)
        for name, enabled in (("off", False), ("on", True))
    }
    for report in runs.values():
        durations = [row["context_ms"] for row in report["cases"] if "context_ms" in row]
        report["median_context_ms"] = median(durations) if durations else None
    return dict(
        evaluation="mock_engineering_only",
        candidate_depth=CANDIDATE_DEPTH,
        token_budget=TOKEN_BUDGET,
        token_estimator="utf8_byte_upper_bound",
        runs=runs,
    )
