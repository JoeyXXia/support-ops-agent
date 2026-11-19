"""Seed the synthetic public demo corpus with deterministic embeddings."""

from pathlib import Path

from packages.knowledge.embeddings import MockEmbedding
from packages.knowledge.ingestion import ingest_version
from packages.knowledge.markdown import load_directory
from packages.knowledge.repository import import_batch
from packages.storage.database import engine_from_environment


def main():
    root = Path(__file__).resolve().parents[1]
    items = load_directory(root / "data/demo/docs", root=root, product_versions=["1.0", "2.0"])
    if not items:
        raise SystemExit("No demo documents found")
    engine = engine_from_environment()
    try:
        versions = import_batch(engine, items, tenant_id=None)
        for version in versions:
            result = ingest_version(engine, version["id"], MockEmbedding())
            if result["status"] != "ready":
                raise SystemExit("Demo seed did not finish; check migrations and local database")
        print(f"Demo seed ready: {len(versions)} public versions (mock embeddings)")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
