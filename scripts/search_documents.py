"""Local operator vector search and reproducible mock development-set evaluation."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.retrieval import compare_context, evaluate
from packages.knowledge.context import CANDIDATE_DEPTH, TOKEN_BUDGET, build_context
from packages.knowledge.embeddings import MockEmbedding
from packages.knowledge.keyword import K1, RRF_K, TOKENIZER, B
from packages.knowledge.retrieval import HYBRID_DEPTH, search_query
from packages.storage.database import engine_from_environment


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="action", required=True)
    search = commands.add_parser("search")
    search.add_argument("question")
    search.add_argument("--tenant-id", choices=["tenant-alpha", "tenant-beta"])
    search.add_argument("--product-version", required=True)
    search.add_argument("--k", type=int, default=5, choices=range(1, 51))
    modes = ("vector", "bm25", "hybrid")
    search.add_argument("--mode", choices=modes, default="vector")
    search.add_argument("--context", action="store_true")
    search.add_argument("--rerank", action="store_true")
    search.add_argument("--context-budget", type=int, default=TOKEN_BUDGET)
    evaluation = commands.add_parser("eval")
    evaluation.add_argument("--cases", type=Path)
    eval_modes = evaluation.add_mutually_exclusive_group()
    eval_modes.add_argument("--compare", action="store_true")
    eval_modes.add_argument("--compare-context", action="store_true")
    args = parser.parse_args()
    if args.action == "search" and (not args.question.strip() or not args.product_version.strip()):
        parser.error("question and product version must not be blank")
    if args.action == "search":
        if args.rerank and not args.context:
            parser.error("--rerank requires --context")
        if args.context and (
            args.k > CANDIDATE_DEPTH
            or not 1 <= args.context_budget <= 8192
            or len(args.question.encode("utf-8")) > 8192
        ):
            parser.error(
                "context requires k <= 20, budget 1..8192 and question <= 8192 UTF-8 bytes"
            )
    provider = MockEmbedding()
    engine = engine_from_environment()
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            if args.action == "eval":
                path = args.cases or Path(__file__).resolve().parents[1] / "evals/dev.jsonl"
                if args.compare_context:
                    output = compare_context(conn, path, provider)
                elif args.compare:
                    datasets = (
                        {"custom": path}
                        if args.cases is not None
                        else {"dev": path, "exact_terms": path.with_name("day-11-exact.jsonl")}
                    )
                    output = dict(
                        evaluation="mock_engineering_only",
                        parameters=dict(
                            tokenizer=TOKENIZER,
                            k1=K1,
                            b=B,
                            rrf_k=RRF_K,
                            hybrid_depth=HYBRID_DEPTH,
                            k=5,
                        ),
                        suites={
                            name: {
                                mode: evaluate(conn, dataset, provider, mode=mode) for mode in modes
                            }
                            for name, dataset in datasets.items()
                        },
                    )
                else:
                    output = evaluate(conn, path, provider)
            else:
                results = search_query(
                    conn,
                    args.question,
                    provider,
                    tenant_id=args.tenant_id,
                    product_version=args.product_version,
                    k=CANDIDATE_DEPTH if args.context else args.k,
                    mode=args.mode,
                )
                output = dict(provider=provider.provider, model=provider.model, results=results)
                if args.context:
                    output.pop("results")
                    output["context"] = build_context(
                        args.question,
                        results,
                        rerank=args.rerank,
                        token_budget=args.context_budget,
                        limit=args.k,
                    )
                if args.mode != "vector":
                    output["mode"] = args.mode
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
