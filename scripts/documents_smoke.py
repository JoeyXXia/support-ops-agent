"""Print counts and a durable digest for a fresh-process/restart verification."""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sqlalchemy as sa

from packages.knowledge.repository import query_versions
from packages.storage.database import engine_from_environment


def main():
    engine = engine_from_environment()
    try:
        rows = query_versions(engine, tenant_id=None)
        durable = [{key: str(value) for key, value in row.items()} for row in rows]
        with engine.connect() as conn:
            server = conn.scalar(sa.text("SHOW server_version"))
            vector = conn.scalar(
                sa.text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            )
        print(
            json.dumps(
                {
                    "server_version": server,
                    "pgvector_version": vector,
                    "public_version_count": len(rows),
                    "public_document_count": len({row["document_id"] for row in rows}),
                    "durable_digest": hashlib.sha256(
                        json.dumps(durable, sort_keys=True).encode()
                    ).hexdigest(),
                },
                indent=2,
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
