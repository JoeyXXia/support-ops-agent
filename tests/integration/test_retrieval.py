from dataclasses import replace

from support import PostgreSQLCase

from packages.knowledge.embeddings import DIMENSIONS, EmbeddingBatch, EmbeddingError, MockEmbedding
from packages.knowledge.ingestion import ingest_version
from packages.knowledge.repository import import_batch
from packages.knowledge.retrieval import search_chunks, search_query
from packages.storage.schema import document_versions, embedding_cache

QUERY = [1.0] + [0.0] * (DIMENSIONS - 1)
ORTHOGONAL = [0.0, 1.0] + [0.0] * (DIMENSIONS - 2)


class RetrievalTests(PostgreSQLCase):
    def ingest(self, item, tenant=None, vector=QUERY, model=MockEmbedding.model):
        item_id = import_batch(self.engine, [item], tenant_id=tenant)[0]["id"]
        provider = MockEmbedding()
        provider.model = model
        provider.embed = lambda texts: EmbeddingBatch([vector] * len(texts))
        self.assertEqual(
            ingest_version(self.engine, item_id, provider, tenant_id=tenant)["status"], "ready"
        )
        return item_id

    def search(self, tenant="tenant-alpha", version="2.0", k=5, vector=QUERY):
        with self.engine.connect() as conn:
            return search_chunks(
                conn,
                vector,
                tenant_id=tenant,
                product_version=version,
                provider=MockEmbedding.provider,
                model=MockEmbedding.model,
                k=k,
            )

    def test_distance_ties_and_citations(self):
        first, second = self.items[:2]
        self.ingest(first, vector=ORTHOGONAL)
        version_id = self.ingest(second)
        results = self.search(k=3)
        self.assertEqual([row["score"] for row in results], [1, 1, 0])
        self.assertEqual([row["ordinal"] for row in results], [0, 1, 0])
        self.assertEqual(results, self.search(k=3))
        section = results[1]
        self.assertEqual(section["version_id"], version_id)
        self.assertEqual(section["source"], f"{second.document_key}#{section['heading_path'][-1]}")
        self.assertEqual(section["body"], second.body[section["start_char"] : section["end_char"]])

    def test_scope_and_product_version_filter_before_limit(self):
        public, alpha, beta, old = self.items[:4]
        self.ingest(public, vector=ORTHOGONAL)
        self.ingest(alpha, tenant="tenant-alpha", vector=ORTHOGONAL)
        self.ingest(beta, tenant="tenant-beta")
        self.ingest(replace(old, product_versions=("1.0",)))
        self.assertEqual(
            {row["scope_key"] for row in self.search()}, {"public", "tenant:tenant-alpha"}
        )
        self.assertEqual(
            {row["document_key"] for row in self.search()},
            {public.document_key, alpha.document_key},
        )
        self.assertEqual(
            self.search(tenant=None, version="1.0", k=1)[0]["document_key"], old.document_key
        )
        self.assertEqual(self.search(version="9.9"), [])

    def test_latest_applicable_revision_and_profile(self):
        item = self.items[0]
        initial_id = self.ingest(item)
        newer = replace(item, product_versions=("2.0",), revision_hash="a" * 64)
        new_id = import_batch(self.engine, [newer], tenant_id=None)[0]["id"]
        self.assertEqual(self.search(), [])
        self.assertEqual({row["version_id"] for row in self.search(version="1.0")}, {initial_id})
        self.ingest(newer, model="other-model")
        self.assertEqual(self.search(), [])
        self.ingest(newer)
        self.assertEqual({row["version_id"] for row in self.search()}, {new_id})
        with self.engine.begin() as conn:
            conn.execute(
                document_versions.update()
                .where(document_versions.c.id == new_id)
                .values(embedding_status="failed")
            )
        self.assertEqual(self.search(), [])

    def test_cache_scope_mismatch_and_invalid_query(self):
        self.ingest(self.items[0])
        with self.engine.begin() as conn:
            conn.execute(embedding_cache.update().values(scope_key="tenant:tenant-beta"))
        self.assertEqual(self.search(), [])
        with self.assertRaises(EmbeddingError):
            self.search(vector=[1.0])
        with self.assertRaisesRegex(ValueError, "product_version_required"):
            self.search(version=" ")

    def test_all_modes_share_scope_version_and_latest_ready(self):
        public, alpha, beta, old, pending = self.items[:5]
        self.ingest(public)
        self.ingest(alpha, tenant="tenant-alpha")
        self.ingest(beta, tenant="tenant-beta")
        self.ingest(replace(old, product_versions=("1.0",)))
        self.ingest(pending)
        import_batch(self.engine, [replace(pending, revision_hash="b" * 64)], tenant_id=None)
        with self.engine.connect() as conn:
            for mode in ("vector", "bm25", "hybrid"):
                with self.subTest(mode=mode):
                    rows = search_query(
                        conn,
                        "clouddesk-demo-v1 适用版本",
                        MockEmbedding(),
                        mode=mode,
                        tenant_id="tenant-alpha",
                        product_version="2.0",
                        k=50,
                    )
                    self.assertEqual(
                        {row["document_key"] for row in rows},
                        {public.document_key, alpha.document_key},
                    )
                    self.assertEqual(len({row["chunk_id"] for row in rows}), len(rows))
