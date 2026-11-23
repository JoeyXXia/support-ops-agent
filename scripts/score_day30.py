"""Export per-observation behavior and retrieval results against Day29 dev IDs."""

import argparse
import json
from pathlib import Path

from evals.behavior import ExpectedRun, score_behavior, score_retrieval
from scripts.freeze_day29 import ROOT, freeze, materialize


def score_rows(cases, samples):
    results = []
    seen = set()
    for sample in samples:
        sample_id = sample["sample_id"]
        if sample_id in seen:
            raise ValueError(f"Duplicate sample ID: {sample_id}")
        seen.add(sample_id)
        expected = ExpectedRun.model_validate(sample["expected"])
        if expected.case_id not in cases:
            raise ValueError(f"Not a Day29 development case: {expected.case_id}")
        row = {"sample_id": sample_id, "case_id": expected.case_id}
        row["behavior"] = score_behavior(expected, sample["observation"])
        if "ranked_sources" in sample:
            row["retrieval"] = score_retrieval(cases[expected.case_id], sample["ranked_sources"])
        results.append(row)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    frozen = freeze(ROOT)
    materialize(frozen, ROOT / "evals/day29")
    cases = {case["id"]: case for case in map(json.loads, frozen["dev.jsonl"].splitlines())}
    source = json.loads(args.observations.read_text(encoding="utf-8"))
    results = score_rows(cases, source["samples"])
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(
            {
                "version": "day30-v1",
                "dataset_version": "day29-v1",
                "mode": source["mode"],
                "results": results,
            },
            stream,
            ensure_ascii=False,
            indent=2,
        )
        stream.write("\n")
    print(f"Exported {len(results)} observations")


if __name__ == "__main__":
    main()
