"""Validate offline demo relationships; accepts a separate fixture root for fault checks."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.schema import EvalCase


def validate(root: Path) -> dict:
    demo = root / "data/demo"
    facts = json.loads((demo / "product_facts.json").read_text(encoding="utf-8"))
    tenants = json.loads((demo / "tenants.json").read_text(encoding="utf-8"))
    tickets = [
        json.loads(x)
        for x in (demo / "tickets.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    cases = [
        EvalCase.model_validate_json(x)
        for x in (root / "evals/dev.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def unique(values, label):
        require(len(values) == len(set(values)), f"duplicate {label}")

    require(facts["synthetic"] is True, "facts must be synthetic")
    require(len(tenants) == 2, "expected two tenants")
    unique([t["id"] for t in tenants], "tenant id")
    workspaces = {}
    users = []
    for tenant in tenants:
        require(tenant["synthetic"] is True, "tenant must be synthetic")
        for user in tenant["users"]:
            users.append(user["id"])
            require(user["role"] in facts["roles"], "unknown role")
        for workspace in tenant["workspaces"]:
            require(workspace["id"] not in workspaces, "duplicate workspace id")
            require(workspace["plan"] in facts["plans"], "unknown plan")
            require(workspace["version"] in facts["versions"], "unknown workspace version")
            for key in ("minute_requests", "monthly_requests"):
                require(type(workspace[key]) is int and workspace[key] >= 0, "invalid usage")
            workspaces[workspace["id"]] = tenant["id"]
    unique(users, "user id")
    require(len(tickets) == 10, "expected ten tickets")
    unique([t["id"] for t in tickets], "ticket id")
    for ticket in tickets:
        require(ticket["synthetic"] is True, "ticket must be synthetic")
        require(
            workspaces.get(ticket["workspace_id"]) == ticket["tenant_id"],
            f"ticket tenant mismatch: {ticket['id']}",
        )
        require(
            ticket["status"] == "resolved" and bool(ticket["resolution"].strip()),
            "invalid historical resolution",
        )
    docs = list((demo / "docs").glob("*.md"))
    require(len(docs) == 8, "expected eight documents")
    sources = set()
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        require("Synthetic：true" in text, f"missing synthetic marker: {doc.name}")
        require(f"文档 ID：{doc.stem}" in text, f"document id mismatch: {doc.name}")
        require(facts["data_version"] in text, f"data version mismatch: {doc.name}")
        for line in text.splitlines():
            if line.startswith("## "):
                sources.add(doc.stem + "#" + line[3:].strip())
    require(len(cases) == 12, "expected twelve dev cases")
    unique([c.id for c in cases], "case id")
    tenants_ids = {t["id"] for t in tenants}
    for case in cases:
        require(case.tenant_id in tenants_ids, f"unknown tenant: {case.id}")
        require(
            case.product_version is None or case.product_version in facts["versions"],
            f"unknown version: {case.id}",
        )
        for source in case.relevant_sources:
            require(source in sources, f"unknown source: {case.id}: {source}")
    uncertain = sum(c.answerability in {"clarify", "abstain"} for c in cases)
    require(uncertain >= 3, "expected at least three clarification/unknown cases")
    return {
        "data_version": facts["data_version"],
        "tenants": len(tenants),
        "tickets": len(tickets),
        "documents": len(docs),
        "dev_cases": len(cases),
        "clarify_or_abstain": uncertain,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.root), ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        sys.exit(1)
