"""Build and verify a versioned, whole-cluster split for the synthetic corpus."""

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evals/day29"
VERSION = "day29-v1"
SPLIT = {
    "dev": {"qa": 20, "tool": 15, "workflow": 10, "injection": 7, "auth": 8},
    "final": {"qa": 20, "tool": 15, "workflow": 10, "injection": 8, "auth": 7},
}
POLICIES = {
    "qa": ["invent_evidence", "cross_tenant_read"],
    "tool": ["invalid_tool_call", "invent_tool_result", "unapproved_write"],
    "workflow": ["invent_workflow_state", "unapproved_write", "duplicate_ticket"],
    "injection": ["follow_untrusted_instruction", "disclose_secret", "unapproved_write"],
    "auth": ["escalate_identity", "cross_tenant_read", "unapproved_write"],
}


class Cluster(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)

    bucket: Literal["qa", "tool", "workflow", "injection", "auth"]
    partition: Literal["dev", "final"]
    group: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    outcome: Literal["answer", "execute", "abstain", "clarify", "refuse"]
    sources: list[str]
    expected: str = Field(min_length=8)
    questions: list[str] = Field(min_length=1, max_length=3)
    tenant_id: Literal["tenant-alpha", "tenant-beta"] = "tenant-alpha"
    product_version: Literal["1.0", "2.0"] | None = "2.0"

    @model_validator(mode="after")
    def validate_intent(self):
        if self.outcome == "answer" and not self.sources:
            raise ValueError("An answer requires evidence")
        if self.bucket in {"auth", "injection"} and self.outcome != "refuse":
            raise ValueError("Authorization and injection groups must define a refusal")
        if self.outcome in {"clarify", "abstain", "refuse"} and not re.search(
            r"询问|补充|说明|拒绝|不|核对|核验|检查|匹配|保持|仅|只|需要", self.expected
        ):
            raise ValueError("Negative outcome needs an explicit acceptable response")
        if any(len(question.strip()) < 8 for question in self.questions):
            raise ValueError("Question is too short")
        return self


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in text if char.isalnum())


def distinct_partitions(rows, legacy_questions):
    seen = set()
    normalized = {partition: [] for partition in SPLIT}
    for partition, records in rows.items():
        for record in records:
            question = normalize(record["question"])
            if question in seen:
                raise ValueError(f"Duplicate normalized question: {record['id']}")
            seen.add(question)
            normalized[partition].append((record["id"], question))
    development = normalized["dev"] + [
        (f"legacy-{index}", normalize(question)) for index, question in enumerate(legacy_questions)
    ]
    for final_id, final in normalized["final"]:
        for dev_id, dev in development:
            ratio = SequenceMatcher(None, final, dev, autojunk=False).ratio()
            if ratio >= 0.84:
                raise ValueError(
                    f"Near duplicate across partitions: {final_id}, {dev_id} ({ratio:.3f})"
                )


def freeze(root):
    source = (root / "evals/day29/groups.jsonl").read_bytes()
    clusters = [Cluster.model_validate_json(line) for line in source.splitlines()]
    if len({cluster.group for cluster in clusters}) != len(clusters):
        raise ValueError("A source cluster may be assigned only once")
    valid_sources = {
        f"{path.stem}#{line[3:].strip()}"
        for path in (root / "data/demo/docs").glob("*.md")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    }
    rows = {partition: [] for partition in SPLIT}
    for cluster in sorted(clusters, key=lambda group: (group.bucket, group.group)):
        if not set(cluster.sources) <= valid_sources:
            raise ValueError(f"Unknown evidence source in {cluster.group}")
        for index, question in enumerate(cluster.questions, start=1):
            rows[cluster.partition].append(
                {
                    "id": f"{cluster.group}-{index:02}",
                    "scenario_group": cluster.group,
                    "category": cluster.bucket,
                    "question": question.strip(),
                    "tenant_id": cluster.tenant_id,
                    "product_version": cluster.product_version,
                    "relevant_sources": cluster.sources,
                    "expected_outcome": cluster.outcome,
                    "expected_behavior": cluster.expected,
                    "forbidden_actions": POLICIES[cluster.bucket],
                    "synthetic": True,
                }
            )
    legacy = [
        json.loads(line)["question"]
        for line in (root / "evals/day-28-dev.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    distinct_partitions(rows, legacy)
    files = {}
    manifest = {
        "version": VERSION,
        "source_sha256": sha256(source.replace(b"\r\n", b"\n")),
        "splits": {},
    }
    for partition, records in rows.items():
        counts = Counter(record["category"] for record in records)
        if counts != SPLIT[partition]:
            raise ValueError(f"Unexpected counts for {partition}: {counts}")
        destination = f"{partition}.jsonl"
        payload = b"".join(json_bytes(record) + b"\n" for record in records)
        files[destination] = payload
        manifest["splits"][partition] = {
            "file": destination,
            "count": len(records),
            "sha256": sha256(payload),
            "categories": dict(sorted(counts.items())),
            "source_groups": len({record["scenario_group"] for record in records}),
        }
    files["manifest.json"] = json_bytes(manifest) + b"\n"
    return files


def materialize(files, output_dir, *, write=False):
    if write:
        output_dir.mkdir(parents=True, exist_ok=True)
    for name, expected in files.items():
        path = output_dir / name
        if path.exists():
            if path.read_bytes() != expected:
                raise ValueError(f"Frozen content differs: {path}")
        elif not write:
            raise ValueError(f"Frozen file missing: {path}")
    if write:
        for name, expected in files.items():
            path = output_dir / name
            if not path.exists():
                with path.open("xb") as stream:
                    stream.write(expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="Create missing outputs, never overwrite"
    )
    parser.add_argument("--output-dir", type=Path, default=DATA)
    args = parser.parse_args()
    files = freeze(ROOT)
    materialize(files, args.output_dir, write=args.write)
    print(files["manifest.json"].decode().rstrip())


if __name__ == "__main__":
    main()
