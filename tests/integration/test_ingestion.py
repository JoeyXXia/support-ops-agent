from threading import Event, Thread

import sqlalchemy as sa
from support import PostgreSQLCase

from packages.knowledge.embeddings import EmbeddingBatch, EmbeddingError, MockEmbedding
from packages.knowledge.ingestion import ingest_version
from packages.knowledge.repository import import_batch
from packages.storage.schema import chunks, document_versions, embedding_cache


class Counting(MockEmbedding):
    def __init__(self, fail_at=None):
        self.calls = 0
        self.fail_at = fail_at

    def embed(self, texts):
        self.calls += 1
        if self.calls == self.fail_at:
            raise EmbeddingError("timeout", attempts=1)
        return super().embed(texts)


class IngestionDatabaseTests(PostgreSQLCase):
    def version(self, tenant_id=None):
        return import_batch(self.engine, self.items[:1], tenant_id=tenant_id)[0]["id"]

    def count(self, table):
        with self.engine.connect() as conn:
            return conn.scalar(sa.select(sa.func.count()).select_from(table))

    def test_reuse_cache_and_keep_chunk_ids(self):
        version = self.version()
        provider = Counting()
        first = ingest_version(self.engine, version, provider)
        with self.engine.connect() as conn:
            before = conn.execute(sa.select(chunks.c.id).order_by(chunks.c.ordinal)).scalars().all()
        again = ingest_version(self.engine, version, provider)
        self.assertEqual((first["status"], again["status"]), ("ready", "ready"))
        self.assertEqual(again["batches"], 0)
        self.assertEqual(again["cache_hits"], first["chunks"])
        self.assertEqual(provider.calls, 1)
        with self.engine.connect() as conn:
            self.assertEqual(
                before,
                conn.execute(sa.select(chunks.c.id).order_by(chunks.c.ordinal)).scalars().all(),
            )
            for row in conn.execute(sa.select(chunks)).mappings():
                self.assertEqual(
                    self.items[0].body[row["start_char"] : row["end_char"]], row["body"]
                )

    def test_partial_failure_then_resume(self):
        version = self.version()
        first = ingest_version(self.engine, version, Counting(fail_at=2), batch_size=1)
        self.assertEqual(first["status"], "failed")
        self.assertEqual(self.count(chunks), 0)
        self.assertEqual(self.count(embedding_cache), 1)
        second_provider = Counting()
        again = ingest_version(self.engine, version, second_provider, batch_size=1)
        self.assertEqual(again["status"], "ready")
        self.assertEqual((again["cache_hits"], second_provider.calls), (1, 1))

    def test_invalid_vector_never_cached(self):
        provider = Counting()
        provider.embed = lambda texts: EmbeddingBatch([[float("nan")] * 1536] * len(texts))
        result = ingest_version(self.engine, self.version(), provider)
        self.assertEqual((result["status"], result["error"]), ("failed", "invalid_output"))
        self.assertEqual(self.count(chunks), 0)
        self.assertEqual(self.count(embedding_cache), 0)

    def test_scope_and_provider_cache_separation(self):
        public, alpha = self.version(), self.version("tenant-alpha")
        provider = Counting()
        a = ingest_version(self.engine, public, provider)
        b = ingest_version(self.engine, alpha, provider, tenant_id="tenant-alpha")
        self.assertEqual((a["cache_hits"], b["cache_hits"]), (0, 0))
        self.assertEqual(self.count(embedding_cache), a["chunks"] + b["chunks"])
        other = Counting()
        other.model = "other-fixture-v1"
        self.assertEqual(ingest_version(self.engine, public, other)["cache_hits"], 0)
        mock_openai = Counting()
        mock_openai.provider = "test-openai-namespace"
        self.assertEqual(ingest_version(self.engine, public, mock_openai)["cache_hits"], 0)

    def test_unauthorized_version_never_calls_provider(self):
        beta = self.version("tenant-beta")
        provider = Counting()
        with self.assertRaises(ValueError):
            ingest_version(self.engine, beta, provider, tenant_id="tenant-alpha")
        self.assertEqual(provider.calls, 0)

    def test_one_writer_per_version(self):
        version = self.version()
        entered, release = Event(), Event()
        provider = Counting()
        real_embed = provider.embed

        def wait(texts):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test_wait_expired")
            return real_embed(texts)

        provider.embed = wait
        results = []
        worker = Thread(
            target=lambda: results.append(ingest_version(self.engine, version, provider))
        )
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaisesRegex(ValueError, "version_busy"):
                ingest_version(self.engine, version, Counting())
        finally:
            release.set()
            worker.join(10)
        self.assertEqual(results[0]["status"], "ready")

    def test_publish_rollback_and_cached_retry(self):
        version = self.version()

        def fail_insert(conn, clause, *args, **kw):
            if getattr(getattr(clause, "table", None), "name", None) == "chunks":
                raise RuntimeError("injected publish failure")

        sa.event.listen(self.engine, "before_execute", fail_insert)
        try:
            first = ingest_version(self.engine, version, Counting())
        finally:
            sa.event.remove(self.engine, "before_execute", fail_insert)
        self.assertEqual(first["status"], "failed")
        self.assertEqual(self.count(chunks), 0)
        with self.engine.connect() as conn:
            self.assertEqual(conn.scalar(sa.select(document_versions.c.embedding_status)), "failed")
        again = ingest_version(self.engine, version, Counting())
        self.assertEqual((again["status"], again["batches"]), ("ready", 0))
