"""Capture the Day31 QA development slice with the existing offline knowledge graph."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy.engine import make_url

from packages.agent.graph import GraphAgent
from packages.agent.knowledge_mock import KnowledgeScenarioMock
from packages.tools.executor import ToolExecutor
from packages.tools.schemas import AuthContext
from scripts.freeze_day29 import ROOT, freeze, materialize


async def observe(case):
    context = AuthContext(
        user_id={"tenant-alpha": "user-alpha", "tenant-beta": "user-beta"}[case["tenant_id"]],
        tenant_id=case["tenant_id"],
        role="member",
    )
    agent = GraphAgent(
        KnowledgeScenarioMock(case["question"], case["product_version"]), ToolExecutor()
    )
    result = await agent.run(case["question"], context=context, grounded=True)
    evidence = [
        source
        for event in result.events
        if event["kind"] == "tool.finished"
        and event["tool"] == "search_docs"
        and event["result"]["ok"]
        for source in event["result"]["data"]["sources"]
    ]
    return {
        "case_id": case["id"],
        "question": case["question"],
        "product_version": case["product_version"],
        "expected_outcome": case["expected_outcome"],
        "expected_behavior": case["expected_behavior"],
        "expected_sources": case["relevant_sources"],
        "status": result.status,
        "reason": result.reason,
        "answer": result.answer,
        "citations": result.citations,
        "retrieved_context": evidence,
        "tool_calls": result.tool_calls,
        "model_calls": result.model_calls,
    }


async def capture(cases):
    return [await observe(case) for case in cases]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    url = make_url(os.environ.get("DATABASE_URL") or "")
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or not (url.database or "").endswith("_test")
    ):
        parser.error("Explicit DATABASE_URL must name a loopback PostgreSQL _test database")
    corpus = freeze(ROOT)
    materialize(corpus, ROOT / "evals/day29")
    data = corpus["dev.jsonl"]
    qa = [row for row in map(json.loads, data.splitlines()) if row["category"] == "qa"]
    control = {
        **next(case for case in qa if case["id"] == "starter-limits-01"),
        "id": "legacy-starter-positive-control",
        "question": "starter配额是多少？",
    }
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        results = asyncio.run(capture([*qa, control]))
        if any(result["status"] != "completed" for result in results):
            raise RuntimeError("Knowledge capture failed; check the fixture database")
        json.dump(
            {
                "dataset_version": "day29-v1",
                "dev_sha256": hashlib.sha256(data).hexdigest(),
                "provider": "knowledge-mock-v1",
                "retriever": "bm25",
                "scope": "20 frozen dev QA cases plus one legacy positive control",
                "live_requests": 0,
                "cases": results[:-1],
                "positive_control": results[-1],
            },
            stream,
            ensure_ascii=False,
            indent=2,
        )
        stream.write("\n")
    print(f"Captured {len(qa)} development QA cases and one positive control")


if __name__ == "__main__":
    main()
