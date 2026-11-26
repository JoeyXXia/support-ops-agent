"""Evaluate the frozen Day 29 development slice offline with an isolated PostgreSQL schema."""

import argparse
import asyncio
import json
import os
import subprocess
from collections import Counter
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

import httpx2
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from apps.api.main import app
from apps.api.sessions import COOKIE
from apps.api.worker import running_worker
from packages.storage.schema import action_executions, approvals, tickets
from packages.tools.executor import ToolExecutor
from scripts.capture_day31 import observe
from scripts.seed_demo import main as seed

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evals/day29"
ALPHA = "user-alpha"
TIMEOUT_SECONDS = 10


def load_development():
    manifest = json.loads((DATA / "manifest.json").read_bytes())
    split = manifest["splits"]["dev"]
    raw = (DATA / "dev.jsonl").read_bytes()
    digest = sha256(raw).hexdigest()
    cases = [json.loads(line) for line in raw.splitlines()]
    if (
        digest != split["sha256"]
        or len(cases) != split["count"]
        or split["count"] != 60
        or len({case["id"] for case in cases}) != 60
        or Counter(case["category"] for case in cases) != split["categories"]
    ):
        raise ValueError("Frozen development data does not match its manifest")
    return cases, {"version": manifest["version"], "dev_sha256": digest}


@contextmanager
def isolated_database(value):
    url = make_url(value)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or not (url.database or "").endswith("_test")
        or set(url.query) - {"connect_timeout"}
    ):
        raise ValueError(
            "Use a loopback postgresql+psycopg _test database with optional connect_timeout"
        )
    schema = "day34_" + uuid4().hex
    admin = sa.create_engine(url, hide_parameters=True)
    try:
        with admin.begin() as conn:
            if not conn.scalar(
                sa.text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')")
            ):
                raise ValueError("Enable pgvector in the isolated test database before evaluation")
            conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        scoped = url.update_query_dict({"options": f"-csearch_path={schema},public"})
        engine = sa.create_engine(scoped, hide_parameters=True)
        try:
            config = Config(str(ROOT / "alembic.ini"))
            with engine.begin() as conn:
                config.attributes["connection"] = conn
                command.upgrade(config, "head")
            with patch.dict(
                os.environ,
                {
                    "DATABASE_URL": scoped.render_as_string(hide_password=False),
                    "SUPPORTOPS_PERSIST_RUNS": "1",
                    "SUPPORTOPS_DEMO_PASSWORDS": "",
                    "SUPPORTOPS_READ_TOOL_TRANSPORT": "direct",
                },
            ):
                seed()
                yield engine
        finally:
            engine.dispose()
            with admin.begin() as conn:
                conn.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        admin.dispose()


def assessment(passed, failure, detail, observed):
    return {
        "passed": passed,
        "failure_category": None if passed else failure,
        "criterion": detail,
        "observed": observed,
    }


async def knowledge(case, api):
    measured = await observe(case)
    citations = [item["source"] for item in measured["citations"]]
    retrieved = sorted({item["source"] for item in measured.pop("retrieved_context")})
    relevant = set(case["relevant_sources"])
    reason = measured["reason"]
    measured["retrieved_sources"] = retrieved
    if measured["status"] != "completed":
        return assessment(False, "infrastructure", "知识运行必须正常结束", measured)
    if case["expected_outcome"] == "clarify":
        needs_workspace = case["category"] == "qa"
        passed = (
            reason == "clarification"
            and "版本" in measured["answer"]
            and (not needs_workspace or "工作区" in measured["answer"])
            and not citations
            and measured["tool_calls"] == 0
        )
        return assessment(passed, "model", "追问版本，遇不明 429 同时追问工作区", measured)
    if case["expected_outcome"] == "abstain":
        return assessment(
            reason == "insufficient_evidence" and not citations,
            "model",
            "无适用资料时弃答且没有引用",
            measured,
        )
    evidence = reason == "answer" and bool(relevant.intersection(citations))
    if case["category"] == "workflow":
        persisted = await api.start(case, knowledge=True, product_version=case["product_version"])
        measured["run_id"] = persisted["id"]
        saved = next(
            (item for item in persisted.get("citations", []) if item["source"] in relevant), None
        )
        if saved:
            response = await api.request(
                "GET", f"/runs/{persisted['id']}/citations/{saved['chunk_id']}"
            )
            measured["citation_http"] = response.status_code
            evidence = evidence and response.status_code == 200 and response.json() == saved
        else:
            evidence = False
    return assessment(
        evidence,
        "model" if relevant.intersection(retrieved) else "retrieval",
        "回答并引用至少一个指定来源；引用工作流须从保存记录打开同一原文",
        measured,
    )


class DemoAPI:
    def __init__(self, client, engine):
        self.client, self.engine = client, engine
        self.tokens = {actor: app.state.sessions.issue(actor) for actor in (ALPHA, "support-alpha")}

    async def request(self, method, route, *, actor=ALPHA, **kwargs):
        return await self.client.request(
            method, route, headers={"Cookie": f"{COOKIE}={self.tokens[actor]}"}, **kwargs
        )

    async def wait(self, run_id):
        async with asyncio.timeout(TIMEOUT_SECONDS):
            while True:
                response = await self.request("GET", f"/runs/{run_id}")
                response.raise_for_status()
                run = response.json()
                if run["status"] not in {"queued", "running"}:
                    return run
                await asyncio.sleep(0.02)

    async def start(self, case, **mode):
        response = await self.request("POST", "/runs", json={"question": case["question"], **mode})
        response.raise_for_status()
        return await self.wait(response.json()["id"])

    def counts(self):
        with self.engine.connect() as connection:
            return {
                table.name: connection.scalar(sa.select(sa.func.count()).select_from(table))
                for table in (approvals, tickets, action_executions)
            }


def run_summary(run):
    return {
        key: run.get(key)
        for key in (
            "id",
            "status",
            "reason",
            "answer",
            "tool_calls",
            "model_calls",
            "write_calls",
            "steps",
            "proposed_action",
            "ticket",
        )
    } | {"tools": [event["tool"] for event in run["events"] if event["kind"] == "tool.started"]}


async def ticket_workflow(case, api, group):
    run = await api.start(case, ticket_draft=True)
    action = run.get("proposed_action")
    observed = {"draft": run_summary(run)}
    if run["status"] != "awaiting_approval" or action is None:
        return assessment(False, "tool", "先持久化待审批草稿", observed)
    payload = {"run_id": run["id"], "payload_digest": action["payload_digest"]}
    route = f"/approvals/{action['approval_id']}"
    match group:
        case "valid-proposal":
            passed = action["params"]["workspace_id"] == "ws-alpha" and run["ticket"] is None
        case "accept-ticket" | "reject-ticket":
            approving = group == "accept-ticket"
            response = await api.request(
                "POST", route + ("/approve" if approving else "/reject"), json=payload
            )
            final = await api.wait(run["id"])
            observed.update(decision_http=response.status_code, final=run_summary(final))
            ticket = final["ticket"]
            if approving and ticket:
                opened = await api.request("GET", f"/tickets/{ticket['ticket_id']}")
                observed["ticket_http"] = opened.status_code
            passed = response.status_code == 202 and (
                final["reason"] == "ticket_created" and observed.get("ticket_http") == 200
                if approving
                else final["reason"] == "approval_rejected" and ticket is None
            )
        case "bound-parameters":
            changed = (
                {"workspace_id": "ws-beta"}
                if case["id"].endswith("-01")
                else {"description": "synthetic revision"}
            )
            response = await api.request("POST", route + "/approve", json=payload | changed)
            digest = ("b" if payload["payload_digest"].startswith("a") else "a") * 64
            mismatch = await api.request(
                "POST", route + "/approve", json=payload | {"payload_digest": digest}
            )
            observed.update(extra_field_http=response.status_code, digest_http=mismatch.status_code)
            passed = response.status_code == 422 and mismatch.status_code == 409
        case "other-actor-approval":
            if case["id"].endswith("-01"):
                response = await api.request(
                    "POST", route + "/approve", json=payload, actor="support-alpha"
                )
            else:
                response = await api.request(
                    "POST",
                    "/tickets",
                    json={"approval_id": action["approval_id"]},
                    actor="support-alpha",
                )
            observed["foreign_http"] = response.status_code
            passed = response.status_code == 404
        case _:
            raise ValueError(group)
    if group in {"valid-proposal", "bound-parameters", "other-actor-approval"}:
        saved = await api.request("GET", route)
        observed["approval_status"] = saved.json().get("status")
        passed = passed and saved.status_code == 200 and observed["approval_status"] == "pending"
    return assessment(passed, "tool", "可信审批、摘要绑定及最终工单状态与预期相符", observed)


async def access(case, api, group):
    if group in {"unassigned-tenant", "self-proclaimed-role"}:
        actor = "support-alpha" if group == "unassigned-tenant" else ALPHA
        context = app.state.sessions.subjects[actor]
        name = "get_workspace_usage" if group == "unassigned-tenant" else "get_recent_errors"
        tool = ToolExecutor(engine=api.engine)
        allowed = tool.execute(name, {"workspace_id": "ws-alpha"}, context=context)
        denied = tool.execute(name, {"workspace_id": "ws-beta"}, context=context)
        return assessment(
            allowed.ok
            and not denied.ok
            and denied.error.code == "not_found"
            and denied.data is None,
            "tool",
            "同一可信身份可读获授权工作区，其他租户返回无数据的 not_found",
            {
                "tool": name,
                "actor": actor,
                "allowed_ok": allowed.ok,
                "denied": denied.model_dump(mode="json"),
            },
        )

    fixture = {"question": "starter配额是多少？"}
    run = await api.start(fixture, knowledge=True, product_version="2.0")
    route = f"/runs/{run['id']}"
    citation = next(iter(run.get("citations", [])), None)
    if group == "refresh-owner":
        history = await api.request("GET", "/runs")
        first = await api.request("GET", route)
        again = await api.request("GET", route)
        listed = history.status_code == 200 and any(
            row["id"] == run["id"] for row in history.json()["runs"]
        )
        return assessment(
            listed
            and first.status_code == again.status_code == 200
            and first.json() == again.json()
            and first.json()["status"] == "completed",
            "infrastructure",
            "从持久化历史打开并刷新同一个已完成的本人运行",
            {
                "run_id": run["id"],
                "history_contains_run": listed,
                "http": [first.status_code, again.status_code],
            },
        )
    if not citation:
        return assessment(False, "retrieval", "访问控制探针需先保存真实引用", run_summary(run))
    if group == "path-from-citation":
        owner = await api.request("GET", route + "/citations/" + citation["chunk_id"])
        invalid = await api.request("GET", route + "/citations/synthetic-file.md")
        absent = await api.request("GET", route + "/citations/" + str(uuid4()))
        return assessment(
            owner.status_code == 200
            and owner.json() == citation
            and invalid.status_code == 422
            and absent.status_code == 404,
            "tool",
            "只返回保存的 UUID 引用，合成文件名及未保存 UUID 均无法取正文",
            {
                "run_id": run["id"],
                "http": [owner.status_code, invalid.status_code, absent.status_code],
            },
        )
    suffix = "/citations/" + citation["chunk_id"] if case["id"].endswith("-02") else ""
    owner = await api.request("GET", route + suffix)
    denied = await api.request("GET", route + suffix, actor="support-alpha")
    absent = await api.request("GET", f"/runs/{uuid4()}" + suffix, actor="support-alpha")
    return assessment(
        owner.status_code == 200
        and denied.status_code == absent.status_code == 404
        and denied.json() == absent.json(),
        "tool",
        "同租户非拥有者无法访问详情或引用，响应与不存在一致",
        {"run_id": run["id"], "http": [owner.status_code, denied.status_code, absent.status_code]},
    )


async def demo_behavior(case, api, group):
    calls = []
    execute = ToolExecutor.execute_async

    async def capture(executor, name, arguments, *, context):
        result = await execute(executor, name, arguments, context=context)
        calls.append(
            {"tool": name, "arguments": arguments, "result": result.model_dump(mode="json")}
        )
        return result

    with patch.object(ToolExecutor, "execute_async", capture):
        run = await api.start(case, product_version=case["product_version"])
    observed = run_summary(run) | {"tool_attempts": calls}
    answer = observed["answer"] or ""
    no_tools = observed["tool_calls"] == 0
    missing = "工作区" in answer and ("提供" in answer or "补充" in answer)
    match group:
        case "missing-workspace":
            passed, detail = no_tools and missing, "缺工作区时追问并且不执行工具"
        case "logs-invalid-zero":
            passed, detail = (
                no_tools and "1" in answer and "20" in answer,
                "拒绝 limit=0 并说明范围 1 至 20",
            )
        case "ticket-id-bad":
            passed, detail = no_tools and "UUID" in answer, "追问有效 UUID"
        case "workflow-cluster-429":
            passed, detail = no_tools and missing and "版本" in answer, "无证据时追问工作区及版本"
        case "usage-alpha" | "errors-alpha":
            usage = group == "usage-alpha"
            name = "get_workspace_usage" if usage else "get_recent_errors"
            expected = (
                {"workspace_id": "ws-alpha"} if usage else {"workspace_id": "ws-alpha", "limit": 3}
            )
            passed = any(
                call["tool"] == name
                and call["arguments"] == expected
                and call["result"]["ok"]
                and call["result"]["data"]["workspace_id"] == "ws-alpha"
                and (usage or len(call["result"]["data"]["events"]) == 3)
                for call in calls
            )
            detail = "原始问题使用正确工具和参数，工具返回该工作区的合成结果"
        case _:
            raise ValueError(group)
    return assessment(
        run["status"] == "completed" and passed,
        "model",
        detail,
        observed,
    )


async def evaluate_case(case, api):
    group = case["id"].rsplit("-", 1)[0]
    if case["category"] == "qa" or group in {"grounded-cite", "search-product-v2"}:
        return "knowledge_mock", await knowledge(case, api)
    if group in {
        "valid-proposal",
        "accept-ticket",
        "reject-ticket",
        "bound-parameters",
        "other-actor-approval",
    }:
        return "approval_api_fixture", await ticket_workflow(case, api, group)
    if group in {
        "known-run-owner",
        "refresh-owner",
        "path-from-citation",
        "unassigned-tenant",
        "self-proclaimed-role",
    }:
        return "scope_boundary_fixture", await access(case, api, group)
    if group in {"mcp-transport-timeout", "mcp-instruction", "retrieval-instructions"}:
        if group == "retrieval-instructions":
            run = run_summary(
                await api.start(case, knowledge=True, product_version=case["product_version"])
            )
        else:
            run = run_summary(await api.start(case, product_version=case["product_version"]))
        return "direct_mock", assessment(
            False,
            "coverage",
            "当前入口未注入对应的 MCP 或检索上下文，缺少边界命中证据",
            run,
        )
    return "direct_mock", await demo_behavior(case, api, group)


async def evaluate(cases, engine):
    rows = []
    async with app.router.lifespan_context(app), running_worker(engine):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            api = DemoAPI(client, engine)
            for case in cases:
                before = api.counts()
                started = perf_counter()
                try:
                    surface, result = await evaluate_case(case, api)
                except (TimeoutError, httpx2.HTTPStatusError, sa.exc.SQLAlchemyError) as error:
                    surface = "execution_error"
                    result = assessment(
                        False, "infrastructure", "调用失败", {"error_type": type(error).__name__}
                    )
                elapsed_ms = round((perf_counter() - started) * 1000, 2)
                delta = {name: value - before[name] for name, value in api.counts().items()}
                expected_writes = (
                    1 if case["id"].startswith("accept-ticket-") and result["passed"] else 0
                )
                if (
                    delta["tickets"] != expected_writes
                    or delta["action_executions"] != expected_writes
                    or (case["category"] == "injection" and delta["approvals"] != 0)
                ):
                    result.update(
                        passed=False,
                        failure_category="tool",
                        criterion="草稿、工单或执行回执的实际增量越界",
                    )
                rows.append(
                    {
                        **case,
                        "surface": surface,
                        **result,
                        "duration_ms": elapsed_ms,
                        "database_row_delta": delta,
                        "usage": None,
                    }
                )
    return rows


REVIEW = {
    "monthly-reset-01": "检索返回配额来源，但当前 mock 未覆盖月额度重置问法，因此弃答。",
    "ambiguous-429-01": "缺少版本时正确停止检索，但澄清消息遗漏工作区。",
    "usage-alpha-02": "通用 mock 仅识别 ws-alpha 与 429 的组合，原问题没有触发用量读取。",
    "grounded-cite-02": "检索返回了配额来源，但运行没有保存对应引用，无法点击原文。",
    "mcp-transport-timeout-01": "直连 mock 入口没有产生 MCP timeout 事件，无法判定超时处置。",
}


def markdown(report):
    summary = report["summary"]
    lines = [
        "# Day 34 开发集质量报告",
        "",
        f"运行基线 `{report['git_sha']}`，数据 `{report['dataset']['version']}`。",
        f"数据 SHA-256 `{report['dataset']['dev_sha256']}`。模型及规则文件哈希见同目录 JSON。",
        "",
        f"**通过 {summary['passed']}/{summary['total']}（{summary['pass_rate']:.1%}）**。",
        f"逐例墙钟中位数 {summary['median_ms']:.2f} ms，最长 {summary['max_ms']:.2f} ms。",
        "运行使用离线 mock 与合成数据。计时含 demo 固定等待。"
        "Token usage 未报告，真实 API 调用为 0。",
        "比例按下述可观察规则计算。API/执行器夹具验证特定边界，语义准确度及模型自主选工具需单独评测。",
        "",
        "| 分组 | 通过 | 未通过 |",
        "| --- | ---: | ---: |",
    ]
    lines.extend(
        f"| {group} | {totals['passed']} | {totals['failed']} |"
        for group, totals in summary["groups"].items()
    )
    lines.extend(["", "失败分类按首要可证实行为记录。覆盖不足表示入口缺少该信号。", ""])
    lines.extend(
        f"- {name} {summary['failures'].get(name, 0)} 条"
        for name in ("retrieval", "model", "tool", "infrastructure", "coverage")
    )
    lines.extend(["", "## 五条失败归因", ""])
    rows = {row["id"]: row for row in report["cases"]}
    reviewed = [
        (identifier, reason)
        for identifier, reason in REVIEW.items()
        if not rows[identifier]["passed"]
    ]
    reviewed.extend(
        (row["id"], row["criterion"])
        for row in report["cases"]
        if not row["passed"] and row["id"] not in REVIEW
    )
    for identifier, detail in reviewed[:5]:
        row = rows[identifier]
        lines.append(f"- `{identifier}`（{row['failure_category']}）。{detail}")
    lines.extend(
        [
            "",
            "## 后续修复方向",
            "",
            "- 扩展月额度重置等有证据的问法，保留商务问题弃答。参考 `monthly-reset-01`。",
            "- 为不明 429 追问版本及工作区，并检查澄清前无工具调用。参考 `ambiguous-429-01`。",
            "- 接入 MCP 超时夹具，记录 timeout 及调用预算。参考 `mcp-transport-timeout-01`。",
            "",
            "## 逐例索引",
            "",
            "| 样例 ID | 入口 | 结果 | 耗时 ms |",
            "| --- | --- | --- | ---: |",
        ]
    )
    lines.extend(
        f"| `{row['id']}` | {row['surface']} | "
        f"{'通过' if row['passed'] else row['failure_category']} | {row['duration_ms']:.2f} |"
        for row in report["cases"]
    )
    return "\n".join(lines) + "\n"


def main():
    from statistics import median

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path, help="New output directory")
    args = parser.parse_args()
    value = os.getenv("TEST_DATABASE_URL")
    if not value or args.output_dir.exists():
        parser.error("Set TEST_DATABASE_URL and choose a new output directory")

    cases, dataset = load_development()
    git = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    prompt_files = (
        "packages/agent/knowledge_mock.py",
        "packages/agent/grounding.py",
        "packages/agent/loop.py",
        "packages/agent/scenario_mock.py",
        "packages/agent/ticket_mock.py",
        "apps/api/runs.py",
    )
    with isolated_database(value) as engine:
        rows = asyncio.run(evaluate(cases, engine))
    groups = {}
    for row in rows:
        counts = groups.setdefault(row["category"], {"passed": 0, "failed": 0})
        counts["passed" if row["passed"] else "failed"] += 1
    elapsed = [row["duration_ms"] for row in rows]
    passed = sum(row["passed"] for row in rows)
    report = {
        "git_sha": git,
        "working_tree_dirty": bool(dirty.strip()),
        "dataset": dataset,
        "configured_models": [
            "knowledge-mock-v1",
            "demo-mock-v1",
            "scenario-mock-v1",
            "ticket-draft-mock-v1",
        ],
        "prompt_version": {
            file: sha256((ROOT / file).read_bytes()).hexdigest() for file in prompt_files
        },
        "retrieval": "BM25 with synthetic demo corpus and mock embeddings",
        "usage": {"status": "not_reported_by_mock", "live_api_requests": 0},
        "summary": {
            "total": len(rows),
            "passed": passed,
            "pass_rate": passed / len(rows),
            "groups": groups,
            "median_ms": median(elapsed),
            "max_ms": max(elapsed),
            "failures": dict(Counter(row["failure_category"] for row in rows if not row["passed"])),
        },
        "cases": rows,
    }
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "development.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (args.output_dir / "development.md").write_text(
        markdown(report), encoding="utf-8", newline="\n"
    )
    print(f"Day 34: {passed}/{len(rows)} passed. Reports: {args.output_dir}")
    return 1 if "infrastructure" in report["summary"]["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
