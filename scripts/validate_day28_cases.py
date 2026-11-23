"""Validate the Day28 development corpus without making model calls."""

import json
from pathlib import Path

from evals.schema import EvalCase


def load(path):
    return [
        EvalCase.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]


def main():
    root = Path(__file__).resolve().parents[1]
    baseline = load(root / "evals/day-14-rag.jsonl")
    cases = load(root / "evals/day-28-dev.jsonl")
    sources = {
        path.stem + "#" + line[3:].strip()
        for path in (root / "data/demo/docs").glob("*.md")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    }
    tenants = {
        tenant["id"]
        for tenant in json.loads((root / "data/demo/tenants.json").read_text(encoding="utf-8"))
    }
    if cases[: len(baseline)] != baseline or not 40 <= len(cases) <= 50:
        raise ValueError("Expected the unchanged Day14 prefix and 40–50 development cases")
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Duplicate development case ID")
    if len({case.question for case in cases}) != len(cases):
        raise ValueError("Duplicate development question")
    for case in cases:
        if case.tenant_id not in tenants or case.product_version not in (None, "1.0", "2.0"):
            raise ValueError(f"Invalid scope for {case.id}")
        if not set(case.relevant_sources) <= sources:
            raise ValueError(f"Unknown source for {case.id}")
    print(
        json.dumps(
            {
                "validated": len(cases),
                "baseline": len(baseline),
                "added": len(cases) - len(baseline),
                "model_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
