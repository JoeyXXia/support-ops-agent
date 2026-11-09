from unittest import TestCase
from unittest.mock import patch

from packages.knowledge.context import build_context


def chunk(key, body):
    return dict(
        chunk_id=key, body=body, source=key, scope_key="public", document_key=key, ordinal=0
    )


class ContextTests(TestCase):
    def test_budget_counts_citations_separators_and_multibyte_text(self):
        rows = [chunk("oversized", "中" * 9000), chunk("a", "中文"), chunk("b", "401")]
        result = build_context("401", rows, token_budget=19)
        self.assertEqual(result["text"], "[a]\n中文\n\n[b]\n401")
        self.assertEqual(result["utf8_bytes"], 19)
        self.assertEqual([row["source"] for row in result["sources"]], ["a", "b"])
        self.assertEqual(build_context("401", rows, token_budget=1)["text"], "")

    def test_duplicates_and_limits(self):
        rows = [chunk("a", "a b"), chunk("b", "a  b"), chunk("a", "changed"), chunk("c", "unique")]
        self.assertEqual(
            [row["chunk_id"] for row in build_context("x", rows)["sources"]], ["a", "c"]
        )
        self.assertEqual(len(build_context("x", rows, limit=1)["sources"]), 1)
        with self.assertRaisesRegex(ValueError, "context_question_exceeds"):
            build_context("中" * 3000, rows)

    def test_opt_in_ranking_and_failure_fallback(self):
        rows = [chunk("a", "unrelated"), chunk("b", "401 401")]
        before = build_context("401", rows, limit=1)
        after = build_context("401", rows, rerank=True, limit=1)
        self.assertEqual(before["sources"][0]["chunk_id"], "a")
        self.assertEqual(after["sources"][0]["chunk_id"], "b")
        self.assertEqual(after["rerank_status"], "applied")
        with patch("packages.knowledge.context._rerank", side_effect=RuntimeError("private")):
            fallback = build_context("401", rows, rerank=True, limit=1)
        self.assertEqual(fallback, dict(before, rerank_status="fallback"))
