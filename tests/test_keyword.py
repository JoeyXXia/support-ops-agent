from math import log1p
from unittest import TestCase

from packages.knowledge.keyword import bm25, fuse_ranks, tokenize


def chunk(key, body):
    return dict(chunk_id=key, document_key=key, scope_key="public", ordinal=0, body=body)


class KeywordTests(TestCase):
    def test_chinese_and_exact_identifiers(self):
        self.assertEqual(
            tokenize("限流４０１ 1401 2.0 2.01 Retry-After CloudDesk minute_rate_limit"),
            [
                "限",
                "流",
                "限流",
                "401",
                "1401",
                "2.0",
                "2.01",
                "retry-after",
                "clouddesk",
                "minute_rate_limit",
            ],
        )

    def test_bm25_score_and_no_partial_match(self):
        rows = [chunk("a", "1401 2.01"), chunk("b", "401 2.0")]
        hits = bm25("401 2.0", rows, k=5)
        self.assertEqual([hit["chunk_id"] for hit in hits], ["b"])
        self.assertAlmostEqual(hits[0]["score"], 2 * log1p(1))
        self.assertEqual(bm25("403", rows, k=5), [])
        self.assertEqual(bm25("?", rows, k=5), [])

    def test_rrf_uses_rank_deduplicates_and_breaks_ties(self):
        a, b, c = (chunk(key, key) for key in "abc")
        results = fuse_ranks([a, b], [c, b], k=2)
        self.assertEqual([row["chunk_id"] for row in results], ["b", "a"])
        self.assertEqual(results[0]["ranks"], {"vector": 2, "bm25": 2})
        self.assertAlmostEqual(results[0]["score"], 2 / 62)
        self.assertEqual(fuse_ranks([], [a], k=1)[0]["ranks"], {"bm25": 1})
