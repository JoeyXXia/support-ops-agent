"""Explicit PostgreSQL-only tests. Refuse a database not ending in _test."""

import os
import unittest
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from packages.knowledge.markdown import load_directory

ROOT = Path(__file__).resolve().parents[2]


class PostgreSQLCase(unittest.TestCase):
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
