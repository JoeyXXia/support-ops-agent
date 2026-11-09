"""Prepare a bounded evidence context from already authorized retrieval results."""

from .keyword import bm25

CANDIDATE_DEPTH = 20
TOKEN_BUDGET = 2048


def _rerank(question, candidates):
    matches = bm25(question, candidates, k=len(candidates))
    original = {row["chunk_id"]: row for row in candidates}
    ranked_ids = {row["chunk_id"] for row in matches}
    return [original[row["chunk_id"]] for row in matches] + [
        row for row in candidates if row["chunk_id"] not in ranked_ids
    ]


def build_context(question, candidates, *, rerank=False, token_budget=TOKEN_BUDGET, limit=5):
    if type(token_budget) is not int or not 1 <= token_budget <= 8192:
        raise ValueError("context_budget_must_be_1_to_8192")
    if type(limit) is not int or not 1 <= limit <= CANDIDATE_DEPTH:
        raise ValueError("context_limit_must_be_1_to_20")
    if len(question.encode("utf-8")) > 8192:
        raise ValueError("context_question_exceeds_8192_utf8_bytes")
    candidates = candidates[:CANDIDATE_DEPTH]
    status = "disabled"
    if rerank:
        try:
            ordered = _rerank(question, candidates)
        except Exception:
            status = "fallback"
        else:
            status = "applied"
            candidates = ordered

    parts, sources = [], []
    seen_ids, seen_texts = set(), set()
    used = 0
    for row in candidates:
        chunk_id = row["chunk_id"]
        body = row["body"]
        normalized = " ".join(body.split())
        if not normalized or chunk_id in seen_ids or normalized in seen_texts:
            continue
        part = f"[{chunk_id}]\n{body}"
        size = len(part.encode("utf-8")) + (2 if parts else 0)
        if used + size > token_budget:
            continue
        used += size
        seen_ids.add(chunk_id)
        seen_texts.add(normalized)
        parts.append(part)
        sources.append({key: value for key, value in row.items() if key != "body"})
        if len(parts) == limit:
            break
    return dict(
        text="\n\n".join(parts),
        sources=sources,
        rerank_status=status,
        token_budget=token_budget,
        utf8_bytes=used,
    )
