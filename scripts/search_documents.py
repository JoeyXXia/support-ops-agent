"""Local operator vector search and reproducible mock development-set evaluation."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.retrieval import evaluate
from packages.knowledge.embeddings import MockEmbedding
from packages.knowledge.retrieval import search_chunks
from packages.storage.database import engine_from_environment


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="action", required=True)
    search = commands.add_parser("search")
    search.add_argument("question")
    search.add_argument("--tenant-id", choices=["tenant-alpha", "tenant-beta"])
    search.add_argument("--product-version", required=True)
    search.add_argument("--k", type=int, default=5, choices=range(1, 51))
    commands.add_parser("eval")
    args = parser.parse_args()
    if args.action == "search" and (not args.question.strip() or not args.product_version.strip()):
        parser.error("question and product version must not be blank")
    provider = MockEmbedding()
    engine = engine_from_environment()
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            if args.action == "eval":
                path = Path(__file__).resolve().parents[1] / "evals/dev.jsonl"
                output = evaluate(conn, path, provider)
            else:
                vector = provider.embed([args.question]).vectors[0]
                results = search_chunks(
                    conn,
                    vector,
                    tenant_id=args.tenant_id,
                    product_version=args.product_version,
                    provider=provider.provider,
                    model=provider.model,
                    k=args.k,
                )
                output = dict(provider=provider.provider, model=provider.model, results=results)
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
