"""Verify Day31's captured workflow and summarize provisional labels."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.freeze_day29 import ROOT, freeze, materialize

DIMENSIONS = {"correctness", "grounding", "citation_support", "abstention"}
RULE = "completion-citation-membership-v1"


def structural_pass(row):
    """Weak comparison rule: completion and citation membership only."""
    if row["status"] != "completed":
        return False
    actual = row["citations"]
    if row["reason"] in {"clarification", "insufficient_evidence", "conflicting_evidence"}:
        return not actual
    candidates = {item["chunk_id"]: item for item in row["retrieved_context"]}
    ids = [item["chunk_id"] for item in actual]
    return (
        row["reason"] == "answer"
        and bool(ids)
        and len(ids) == len(set(ids))
        and all(candidates.get(item["chunk_id"]) == item for item in actual)
    )


def summarize(corpus, capture, labels):
    qa = {row["id"]: row for row in corpus if row["category"] == "qa"}
    cases = capture["cases"]
    case_ids = [case["case_id"] for case in cases]
    review_by_id = {label["case_id"]: label for label in labels}
    if set(qa) != set(case_ids) or set(qa) != set(review_by_id) or len(qa) != 20:
        raise ValueError("Case coverage does not match the frozen QA development slice")
    if len(case_ids) != len(qa) or len(labels) != len(qa):
        raise ValueError("Duplicate observations or reviews")
    comparison = []
    for row in cases:
        case_id = row["case_id"]
        frozen, label = qa[case_id], review_by_id[case_id]
        for field in ("question", "product_version", "expected_behavior", "expected_outcome"):
            if row[field] != frozen[field]:
                raise ValueError(f"Captured label differs for {case_id}: {field}")
        if row["expected_sources"] != frozen["relevant_sources"]:
            raise ValueError(f"Captured sources differ for {case_id}")
        scores = label["scores"]
        if set(scores) != DIMENSIONS or any(
            value is not None and (type(value) is not int or value not in (0, 1, 2))
            for value in scores.values()
        ):
            raise ValueError(f"Invalid dimension scores: {case_id}")
        if (
            label["reviewer_kind"] != "assistant_provisional"
            or label["human_status"] != "pending"
            or not label["rationale"].strip()
        ):
            raise ValueError(f"Invalid review provenance: {case_id}")
        available = {hit["source"] for hit in row["retrieved_context"]}
        if label["evidence_anchor"] not in {None, *available, *row["expected_sources"]}:
            raise ValueError(f"Unrecognized evidence anchor: {case_id}")
        structural = structural_pass(row)
        provisional_full = scores["correctness"] == 2 and all(
            value is None or value == 2 for value in scores.values()
        )
        comparison.append(
            {
                "case_id": case_id,
                "reason": row["reason"],
                "expected_source_in_context": bool(available & set(row["expected_sources"])),
                "structural_rule_pass": structural,
                "provisional_fully_acceptable": provisional_full,
                "disagree": structural != provisional_full,
            }
        )
    control = capture["positive_control"]
    if control["case_id"] != "legacy-starter-positive-control" or not structural_pass(control):
        raise ValueError("Unexpected positive-control evidence")
    if control["reason"] != "answer" or not any(
        cite["source"] == "quotas#limits" for cite in control["citations"]
    ):
        raise ValueError("Positive control did not return the expected citation")
    return {
        "development_qa_outputs": len(cases),
        "assistant_provisional_labels": len(labels),
        "human_labels": 0,
        "human_review_status": "pending",
        "captured_outcomes": dict(Counter(case["reason"] for case in cases)),
        "rule_version": RULE,
        "rule_passes": sum(row["structural_rule_pass"] for row in comparison),
        "provisional_fully_acceptable": sum(
            row["provisional_fully_acceptable"] for row in comparison
        ),
        "disagreements": sum(row["disagree"] for row in comparison),
        "llm_judge": None,
        "positive_control": control["reason"],
        "cases": comparison,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    dataset = freeze(ROOT)
    materialize(dataset, ROOT / "evals/day29")
    dev = dataset["dev.jsonl"]
    capture = json.loads((ROOT / "reports/day-31-observations.json").read_text(encoding="utf-8"))
    if capture["dev_sha256"] != hashlib.sha256(dev).hexdigest():
        raise ValueError("Development hash differs from capture")
    labels = [
        json.loads(line)
        for line in (ROOT / "evals/day31/provisional-reviews.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    result = summarize(map(json.loads, dev.splitlines()), capture, labels)
    if args.output:
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "cases"}))


if __name__ == "__main__":
    main()
