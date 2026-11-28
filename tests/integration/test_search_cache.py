from dataclasses import replace
from unittest.mock import patch

from support import PostgreSQLCase

from packages.knowledge.embeddings import MockEmbedding
from packages.knowledge.ingestion import ingest_version
from packages.knowledge.repository import import_batch
from packages.tools.knowledge import search_docs
from packages.tools.schemas import SearchDocsArgs


class SearchCacheTests(PostgreSQLCase):
    def test_hits_separate_tenants_product_model_and_new_revisions(self):
        item = next(item for item in self.items if item.document_key == "quotas")
        tenant = "tenant-alpha"
        old_id = import_batch(self.engine, [item], tenant_id=tenant)[0]["id"]
        self.assertEqual(
            ingest_version(self.engine, old_id, MockEmbedding(), tenant_id=tenant)["status"],
            "ready",
        )
        args = SearchDocsArgs(question="starter 配额 每分钟 每月", product_version="2.0")
        with patch("packages.tools.knowledge.engine_from_environment", return_value=self.engine):
            miss = search_docs(args, tenant)
            hit = search_docs(args, tenant)
            self.assertEqual((miss.cache_status, hit.cache_status), ("miss", "hit"))
            self.assertTrue(miss.sources)
            self.assertEqual(miss.sources, hit.sources)
            foreign = search_docs(args, "tenant-beta")
            self.assertEqual((foreign.cache_status, foreign.sources), ("miss", []))
            previous = search_docs(args.model_copy(update={"product_version": "1.0"}), tenant)
            self.assertEqual(previous.cache_status, "miss")
            self.assertTrue(all(row.product_version == "1.0" for row in previous.sources))
            with patch.object(MockEmbedding, "model", "fixture-other-v2"):
                changed_model = search_docs(args, tenant)
            self.assertEqual((changed_model.cache_status, changed_model.sources), ("miss", []))

            new = replace(item, product_versions=("2.0",), revision_hash="b" * 64)
            new_id = import_batch(self.engine, [new], tenant_id=tenant)[0]["id"]
            pending = search_docs(args, tenant)
            self.assertEqual((pending.cache_status, pending.sources), ("miss", []))
            self.assertEqual(
                ingest_version(self.engine, new_id, MockEmbedding(), tenant_id=tenant)["status"],
                "ready",
            )
            ready = search_docs(args, tenant)
            self.assertEqual(ready.cache_status, "miss")
            self.assertTrue(ready.sources)
            self.assertTrue(all(row.version_number == 2 for row in ready.sources))
            self.assertEqual(search_docs(args, tenant).cache_status, "hit")
