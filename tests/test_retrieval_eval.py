from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from evals.retrieval import evaluate
from packages.knowledge.embeddings import MockEmbedding


class RetrievalEvalTests(TestCase):
    def test_exact_public_source_and_explicit_denominator(self):
        path = Path(__file__).resolve().parents[1] / "evals/dev.jsonl"
        connection = Mock()
        connection.execute.return_value.mappings.return_value = []
        with patch("evals.retrieval.search_query") as search:
            search.side_effect = [
                [{"scope_key": "public", "source": "quotas"}],
                [{"scope_key": "tenant:tenant-beta", "source": "quotas#limits"}],
                [{"scope_key": "public", "source": "versions#versions"}],
                *([[]] * 7),
            ]
            report = evaluate(connection, path, MockEmbedding())
        self.assertEqual((report["hits"], report["scored"], report["hit_at_5"]), (1, 10, 0.1))
        self.assertIn("dev-001", report["missed_ids"])
        self.assertIn("dev-002", report["missed_ids"])
        self.assertEqual(
            {row["id"]: row["excluded"] for row in report["cases"] if "excluded" in row},
            {"dev-007": "missing_product_version", "dev-008": "no_relevant_sources"},
        )
