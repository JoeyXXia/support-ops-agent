"""Trusted operator CLI; preview is offline, embeddings require explicit --live."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.knowledge.chunking import POLICY, split_markdown
from packages.knowledge.embeddings import MockEmbedding, OpenAIEmbedding
from packages.knowledge.ingestion import ingest_version
from packages.knowledge.markdown import load_directory
from packages.knowledge.repository import query_versions
from packages.storage.database import engine_from_environment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["preview", "embed"])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--tenant-id", choices=["tenant-alpha", "tenant-beta"])
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100 or not 1 <= args.batch_size <= 16:
        parser.error("limit must be 1..100 and batch-size 1..16")
    if args.action == "preview":
        if args.live:
            parser.error("preview never calls API")
        root = Path(__file__).resolve().parents[1]
        result = [
            dict(
                document_key=d.document_key,
                source_path=d.source_path,
                revision_hash=d.revision_hash,
                policy=POLICY,
                chunks=[asdict(c) for c in split_markdown(d.body)],
            )
            for d in load_directory(
                root / "data/demo/docs", root=root, product_versions=["1.0", "2.0"]
            )
        ]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    engine = engine_from_environment()
    provider = None
    try:
        provider = OpenAIEmbedding() if args.live else MockEmbedding()
        # Process latest stored revision of each visible identity, not all historical revisions.
        latest = {}
        for row in query_versions(engine, tenant_id=args.tenant_id):
            latest[(row["scope_key"], row["document_key"])] = row
        results = []
        for row in list(latest.values())[: args.limit]:
            outcome = ingest_version(
                engine, row["id"], provider, tenant_id=args.tenant_id, batch_size=args.batch_size
            )
            results.append(outcome)
            if outcome["status"] != "ready":
                break
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if results and all(r["status"] == "ready" for r in results) else 1
    finally:
        if provider is not None and hasattr(provider, "close"):
            provider.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
