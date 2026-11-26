"""Run a small, explicitly authorized OpenAI evaluation over public synthetic QA."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from time import perf_counter

from packages.agent.models import Message, ModelRequest, ProviderError
from packages.agent.providers import OpenAIProvider
from scripts.evaluate_day34 import load_development

ROOT = Path(__file__).resolve().parents[1]
MODEL = "gpt-4.1-mini"
TIMEOUT_SECONDS = 20
MAX_OUTPUT_TOKENS = 32
MAX_PROMPT_BYTES = 1200
SYSTEM = "仅依据提供的虚构公开资料答题。只输出列出的一项，不添加解释。"
CASES = (
    ("starter-limits-01", "quotas.md", "输出 每分钟次数/每月次数，例如 1/2。", "60/10000"),
    ("monthly-exhaustion-01", "monthly-quota.md", "输出 STOP 或 RETRY。", "STOP"),
    ("commercial-unknown-01", "quotas.md", "无法从资料确定价格时输出 UNKNOWN。", "UNKNOWN"),
)


def prepare():
    cases, dataset = load_development()
    by_id = {case["id"]: case for case in cases}
    prompts = []
    for case_id, filename, response_format, expected in CASES:
        evidence = (ROOT / "data/demo/docs" / filename).read_text(encoding="utf-8")
        case = by_id[case_id]
        if case["synthetic"] is not True or not any(
            line.startswith("Synthetic：true") for line in evidence.splitlines()
        ):
            raise ValueError("Only tagged synthetic fixtures may be scored")
        question = case["question"]
        user = f"资料：\n{evidence}\n\n问题：{question}\n{response_format}"
        if len((SYSTEM + user).encode("utf-8")) > MAX_PROMPT_BYTES:
            raise ValueError("Synthetic prompt exceeds the byte budget")
        request = ModelRequest(
            messages=[Message(role="system", content=SYSTEM), Message(role="user", content=user)],
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        prompts.append((case_id, request, expected))
    return dataset, prompts


async def evaluate(provider, prompts):
    results = []
    for case_id, request, expected in prompts:
        started = perf_counter()
        row = {"id": case_id, "passed": False, "usage": None}
        try:
            result = await provider.generate(request)
        except ProviderError as error:
            row.update(status="provider_error", error=error.code)
        else:
            row["passed"] = (
                not result.tool_calls and result.text.strip().casefold() == expected.casefold()
            )
            row["status"] = "scored"
            if result.usage is not None:
                row["usage"] = result.usage.model_dump()
        row["duration_ms"] = round((perf_counter() - started) * 1000, 2)
        results.append(row)
        if row["status"] == "provider_error":
            break
    return results


async def run(api_key):
    dataset, prompts = prepare()
    provider = OpenAIProvider(MODEL, api_key=api_key, timeout=TIMEOUT_SECONDS)
    try:
        results = await evaluate(provider, prompts)
    finally:
        await provider.close()
    passed = sum(row["passed"] for row in results)
    print(
        json.dumps(
            {
                "mode": "live",
                "git_sha": os.getenv("GITHUB_SHA"),
                "dataset": dataset,
                "rubric": "day35-exact-v1",
                "model": MODEL,
                "budget": {
                    "requests": len(CASES),
                    "output_tokens_per_request": MAX_OUTPUT_TOKENS,
                    "prompt_bytes_per_request": MAX_PROMPT_BYTES,
                    "seconds_per_request": TIMEOUT_SECONDS,
                    "retries": 0,
                },
                "attempted": len(results),
                "passed": passed,
                "cases": results,
            }
        )
    )
    return int(passed != len(CASES))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("Explicit --live required")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print(json.dumps({"mode": "live", "status": "missing_key", "attempted": 0}))
        return 2
    return asyncio.run(run(api_key))


if __name__ == "__main__":
    raise SystemExit(main())
