"""Explicit PostgreSQL-only tests. Refuse a database not ending in _test."""

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from packages.knowledge.markdown import load_directory, load_markdown
from packages.knowledge.repository import import_batch, query_versions
from packages.storage.schema import chunks, document_versions, documents

ROOT = Path(__file__).resolve().parents[2]


class DocumentDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        value = os.getenv("TEST_DATABASE_URL", "")
        if not value:
            raise RuntimeError("Set TEST_DATABASE_URL explicitly; no SQLite or skipped DB tests")
        url = make_url(value)
        if url.drivername != "postgresql+psycopg" or not (url.database or "").endswith("_test"):
            raise RuntimeError("Tests require postgresql+psycopg and a database ending in _test")
        cls.admin = sa.create_engine(url, hide_parameters=True)
        cls.url = url
        cls.items = load_directory(
            ROOT / "data/demo/docs", root=ROOT, product_versions=["1.0", "2.0"]
        )
        with cls.admin.begin() as conn:
            conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))

    @classmethod
    def tearDownClass(cls):
        cls.admin.dispose()

    def setUp(self):
        self.schema = "day8_test_" + uuid4().hex
        with self.admin.begin() as conn:
            conn.execute(sa.text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = sa.create_engine(
            self.url,
            hide_parameters=True,
            connect_args={"options": f"-csearch_path={self.schema},public"},
        )
        self.migrate("upgrade")

    def tearDown(self):
        self.engine.dispose()
        if not self.schema.startswith("day8_test_") or not self.schema[10:].isalnum():
            raise RuntimeError("Unsafe cleanup target")
        with self.admin.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA "{self.schema}" CASCADE'))

    def migrate(self, action):
        cfg = Config(str(ROOT / "alembic.ini"))
        with self.engine.begin() as conn:
            cfg.attributes["connection"] = conn
            getattr(command, action)(cfg, "head" if action == "upgrade" else "base")

    def test_migrations_repeat_and_downgrade(self):
        self.migrate("upgrade")
        tables = set(sa.inspect(self.engine).get_table_names(schema=self.schema))
        self.assertEqual(tables, {"alembic_version", "documents", "document_versions", "chunks"})
        with self.engine.connect() as conn:
            self.assertTrue(
                conn.scalar(sa.text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
            )
        self.migrate("downgrade")
        self.assertNotIn("documents", sa.inspect(self.engine).get_table_names(schema=self.schema))
        self.migrate("upgrade")

    def test_idempotent_batch_and_new_connection(self):
        first = import_batch(self.engine, self.items, tenant_id=None)
        again = import_batch(self.engine, self.items, tenant_id=None)
        self.assertTrue(all(row["created"] for row in first))
        self.assertTrue(all(not row["created"] for row in again))
        self.assertEqual([row["id"] for row in first], [row["id"] for row in again])
        self.engine.dispose()
        rows = query_versions(self.engine, tenant_id=None)
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(row["synthetic"] for row in rows))
        with self.engine.connect() as conn:
            self.assertEqual(conn.scalar(sa.select(sa.func.count()).select_from(chunks)), 0)

    def test_new_content_and_metadata_keep_old_versions(self):
        item = self.items[0]
        import_batch(self.engine, [item], tenant_id=None)
        with tempfile.TemporaryDirectory() as d:
            temp_root = Path(d)
            path = temp_root / item.source_path
            path.parent.mkdir(parents=True)
            path.write_text(item.body + "\nupdated", encoding="utf-8")
            changed = load_markdown(path, root=temp_root, product_versions=["1.0", "2.0"])
            import_batch(self.engine, [changed], tenant_id=None)
            metadata_only = load_markdown(path, root=temp_root, product_versions=["2.0"])
            import_batch(self.engine, [metadata_only], tenant_id=None)
        old = import_batch(self.engine, [item], tenant_id=None)
        self.assertFalse(old[0]["created"])
        rows = query_versions(self.engine, tenant_id=None)
        self.assertEqual([row["version_number"] for row in rows], [1, 2, 3])
        self.assertEqual(rows[0]["body"], item.body)

    def test_batch_failure_rolls_back(self):
        invalid = replace(self.items[1], synthetic=None)
        with self.assertRaises(IntegrityError):
            import_batch(self.engine, [self.items[0], invalid], tenant_id=None)
        with self.engine.connect() as conn:
            for table in (documents, document_versions):
                self.assertEqual(conn.scalar(sa.select(sa.func.count()).select_from(table)), 0)

    def test_public_and_private_scopes(self):
        for tenant in (None, "tenant-alpha", "tenant-beta"):
            import_batch(self.engine, self.items[:1], tenant_id=tenant)
        public = query_versions(self.engine, tenant_id=None)
        alpha = query_versions(self.engine, tenant_id="tenant-alpha")
        self.assertEqual(len(public), 1)
        self.assertEqual({row["scope_key"] for row in alpha}, {"public", "tenant:tenant-alpha"})
        self.assertNotIn("tenant-beta", {row["tenant_id"] for row in alpha})

    def test_concurrent_first_import_no_duplicates(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(import_batch, self.engine, self.items, tenant_id=None) for _ in range(2)
            ]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sum(row["created"] for batch in results for row in batch), 8)
        self.assertEqual(len(query_versions(self.engine, tenant_id=None)), 8)

    def test_concurrent_revisions_numbered(self):
        item = self.items[0]
        import_batch(self.engine, [item], tenant_id=None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    import_batch,
                    self.engine,
                    [replace(item, revision_hash=digit * 64)],
                    tenant_id=None,
                )
                for digit in ("a", "b")
            ]
            [future.result(timeout=20) for future in futures]
        self.assertEqual(
            [row["version_number"] for row in query_versions(self.engine, tenant_id=None)],
            [1, 2, 3],
        )

    def test_constraints(self):
        with self.assertRaises(IntegrityError), self.engine.begin() as conn:
            conn.execute(
                documents.insert().values(
                    id=uuid4(), scope_key="public", tenant_id="tenant-alpha", document_key="bad"
                )
            )
        with self.assertRaises(IntegrityError), self.engine.begin() as conn:
            conn.execute(
                chunks.insert().values(id=uuid4(), version_id=uuid4(), ordinal=0, body="x")
            )
