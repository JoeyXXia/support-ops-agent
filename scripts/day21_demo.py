"""Record real API/SQL approval and child-process recovery in an isolated test schema.

Requires explicit TEST_DATABASE_URL. Never calls a live model or alters shared data.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/integration"))


def main():
    import httpx2
    import sqlalchemy as sa
    from support import PostgreSQLCase

    from apps.api.main import app
    from apps.api.worker import running_worker
    from packages.storage.schema import action_executions, approvals, tickets

    output = ROOT / "reports/day-21-demo.json"
    if output.exists():
        raise FileExistsError("Preserve existing recording; move it explicitly before rerunning")
    events = []
    started = time.monotonic()

    def record(label, **data):
        events.append({"seconds": round(time.monotonic() - started, 3), "step": label, **data})

    PostgreSQLCase.setUpClass()
    fixture = PostgreSQLCase()
    try:
        fixture.setUp()
        try:
            url = fixture.url.update_query_dict(
                {"options": f"-csearch_path={fixture.schema},public"}
            ).render_as_string(hide_password=False)

            def snapshot():
                with fixture.engine.connect() as conn:
                    return {
                        "tickets": conn.scalar(sa.select(sa.func.count()).select_from(tickets)),
                        "receipts": conn.scalar(
                            sa.select(sa.func.count()).select_from(action_executions)
                        ),
                        "approval_status": list(conn.scalars(sa.select(approvals.c.status))),
                    }

            async def approval_demo():
                async def wait(client, run_id):
                    for _ in range(500):
                        response = await client.get(f"/runs/{run_id}")
                        response.raise_for_status()
                        result = response.json()
                        if result["status"] not in {"queued", "running"}:
                            return result
                        await asyncio.sleep(0.02)
                    raise TimeoutError("run did not settle")

                async with (
                    app.router.lifespan_context(app),
                    running_worker(app.state.runs.repository.engine),
                    httpx2.AsyncClient(
                        cookies={"supportops_session": app.state.sessions.issue("user-alpha")},
                        transport=httpx2.ASGITransport(app=app),
                        base_url="http://test",
                    ) as client,
                ):
                    response = await client.post(
                        "/runs", json={"question": "ws-alpha 429", "ticket_draft": True}
                    )
                    assert response.status_code == 202
                    run_id = response.json()["id"]
                    proposal_record = await wait(client, run_id)
                    assert proposal_record["status"] == "awaiting_approval"
                    proposal = proposal_record["proposed_action"]
                    record("提案 / POST /runs 后轮询", run=proposal_record, database=snapshot())
                    body = {
                        "approval_id": proposal["approval_id"],
                        "idempotency_key": proposal["idempotency_key"],
                        "payload_digest": proposal["payload_digest"],
                    }
                    denied = await client.post("/tickets", json=body)
                    assert denied.status_code == 409
                    assert snapshot()["tickets"] == 0
                    record("未批准不能建单 / POST /tickets", http=409, database=snapshot())
                    decision = {"run_id": run_id, "payload_digest": proposal["payload_digest"]}
                    route = f"/approvals/{proposal['approval_id']}/approve"
                    first = await client.post(route, json=decision)
                    assert first.status_code == 202
                    finished = await wait(client, run_id)
                    assert finished["reason"] == "ticket_created"
                    assert snapshot() == {
                        "tickets": 1,
                        "receipts": 1,
                        "approval_status": ["executed"],
                    }
                    record(
                        "批准并恢复 / POST approve 后轮询",
                        http=202,
                        run=finished,
                        database=snapshot(),
                    )
                    for attempt in range(1, 4):
                        repeat = await client.post(route, json=decision)
                        assert repeat.status_code == 202
                        assert (await wait(client, run_id)) == finished
                        replay = await client.post("/tickets", json=body)
                        assert replay.status_code == 201
                        assert snapshot()["tickets"] == snapshot()["receipts"] == 1
                        record(
                            f"重复批准与执行 {attempt}",
                            approval_http=202,
                            execution_http=201,
                            receipt=replay.json(),
                            database=snapshot(),
                        )
                    assert (
                        len(
                            {
                                json.dumps(e["receipt"], sort_keys=True)
                                for e in events
                                if "receipt" in e
                            }
                        )
                        == 1
                    )

            with patch.dict(os.environ, {"DATABASE_URL": url, "SUPPORTOPS_PERSIST_RUNS": "1"}):
                asyncio.run(approval_demo())

            runtime = ROOT / "output/day21-runtime"
            runtime.mkdir(parents=True, exist_ok=True)
            log = runtime / "interrupted.log"
            env = dict(os.environ, DATABASE_URL=url, PYTHONUTF8="1")
            with log.open("w", encoding="utf-8") as stream:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "scripts/checkpoint_run.py",
                        "create",
                        "--pause-at",
                        "after-tools",
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        contents = log.read_text(encoding="utf-8")
                        if '"fault_ready"' in contents or child.poll() is not None:
                            break
                        time.sleep(0.05)
                    assert '"fault_ready": "after-tools"' in contents, "fault point not reached"
                    run_id = json.loads(contents.splitlines()[0])["run_id"]
                    record("只读恢复：工具完成后暂停", run_id=run_id, fault="after-tools")
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=10)

            def cli(action):
                result = subprocess.run(
                    [sys.executable, "scripts/checkpoint_run.py", action, run_id],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=40,
                )
                assert result.returncode == 0, "checkpoint CLI failed; inspect local runtime"
                return json.loads(result.stdout.splitlines()[-1])

            before = cli("show")
            record("子进程已终止 / 新进程 show", run=before, exit_code=child.returncode)
            recovered = cli("resume")
            assert recovered["id"] == run_id
            assert recovered["status"] == "completed"
            assert (recovered["model_calls"], recovered["tool_calls"]) == (2, 2)
            assert snapshot()["tickets"] == snapshot()["receipts"] == 1
            record("新进程 resume 同一 run", run=recovered, database=snapshot())
        finally:
            fixture.tearDown()
    finally:
        PostgreSQLCase.tearDownClass()

    evidence = {
        "day": 21,
        "baseline": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "mode": "recorded ASGI HTTP requests + real PostgreSQL + actual process kill",
        "model": "deterministic mock, no live API",
        "schema_cleanup": "completed",
        "limitations": [
            "HTTP uses in-process ASGI transport; not a browser video or network test",
            "Replay is saved observations, not a live backend",
            "Recovery is explicit; unfinished nodes may execute again",
            "Fixed local demo identity, not production authentication",
        ],
        "events": events,
    }
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    encoded = json.dumps(evidence, ensure_ascii=False).replace("<", "\\u003c")
    page = (ROOT / "scripts/day21_replay.html").read_text(encoding="utf-8")
    (ROOT / "reports/day-21-demo.html").write_text(page.replace("DATA", encoded), encoding="utf-8")
    print(
        json.dumps(
            {
                "recording": str(output.relative_to(ROOT)),
                "events": len(events),
                "tickets": 1,
                "receipts": 1,
                "recovered_run": run_id,
            }
        )
    )


if __name__ == "__main__":
    main()
