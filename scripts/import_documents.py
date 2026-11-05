"""Trusted local operator CLI. Deliberately not exposed through HTTP or Agent tools."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.exc import SQLAlchemyError

from packages.knowledge.markdown import load_directory
from packages.knowledge.repository import import_batch, query_versions
from packages.storage.database import engine_from_environment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["import", "list"])
    parser.add_argument("--scope", choices=["public", "tenant"], required=True)
    parser.add_argument("--tenant-id", choices=["tenant-alpha", "tenant-beta"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--directory", type=Path, default=Path("data/demo/docs"))
    parser.add_argument("--product-version", action="append", default=[])
    args = parser.parse_args()
    if (args.scope == "tenant") != bool(args.tenant_id):
        parser.error("tenant scope requires --tenant-id; public scope forbids it")
    engine = None
    try:
        items = None
        if args.action == "import":
            items = load_directory(
                args.root / args.directory, root=args.root, product_versions=args.product_version
            )
        engine = engine_from_environment()
        rows = (
            import_batch(engine, items, tenant_id=args.tenant_id)
            if items is not None
            else query_versions(engine, tenant_id=args.tenant_id)
        )
        print(json.dumps(rows, ensure_ascii=False, default=str, indent=2))
        return 0
    except (ValueError, OSError, SQLAlchemyError):
        # Never expose database URLs, passwords, query parameters, or private source bodies.
        print(
            "Document operation failed; check configuration, metadata and migrations.",
            file=sys.stderr,
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
