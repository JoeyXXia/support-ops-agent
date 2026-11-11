"""Real PostgreSQL and actual child-process termination for read-only recovery."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx2
from support import ROOT, PostgreSQLCase

from apps.api.main import app
from packages.agent.persistence import RunBusy, RunRepository, execute_run
from packages.agent.scenario_mock import SupportScenarioMock
from packages.tools.schemas import AuthContext


class CheckpointTests(PostgreSQLCase):
    def connection_url(self):
        return self.url.update_query_dict(
            {"options": f"-csearch_path={self.schema},public"}
        ).render_as_string(hide_password=False)

    def test_process_exit_before_after_model_and_after_tools(self):
        env = dict(os.environ, DATABASE_URL=self.connection_url(), PYTHONUTF8="1")
        for point in ("before-model", "after-model", "after-tools"):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                log = Path(directory) / "child.log"
                with log.open("w", encoding="utf-8") as stream:
                    child = subprocess.Popen(
                        [
                            sys.executable,
                            "scripts/checkpoint_run.py",
                            "create",
                            "--pause-at",
                            point,
                        ],
                        cwd=ROOT,
                        env=env,
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                    )
                    try:
                        deadline = time.monotonic() + 25
                        while time.monotonic() < deadline:
                            output = log.read_text(encoding="utf-8")
                            if '"fault_ready"' in output or child.poll() is not None:
                                break
                            time.sleep(0.05)
                        self.assertIn('"fault_ready"', output, output)
                        run_id = json.loads(output.splitlines()[0])["run_id"]
                    finally:
                        if child.poll() is None:
                            child.kill()
                        child.wait(timeout=10)
                resumed = subprocess.run(
                    [sys.executable, "scripts/checkpoint_run.py", "resume", run_id],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                )
                self.assertEqual(resumed.returncode, 0, resumed.stderr)
                record = json.loads(resumed.stdout.splitlines()[-1])
                self.assertEqual(
                    (record["status"], record["model_calls"], record["tool_calls"]),
                    ("completed", 2, 2),
                )
                self.assertEqual(
                    record["state_path"], ["classify", "call_tool", "classify", "answer", "done"]
                )
                self.assertEqual(len(record["events"]), 4)
                self.assertIn("60/60", record["answer"])

    def test_owner_check_precedes_checkpoint_and_concurrent_lock(self):
        repository = RunRepository(self.engine)
        owner = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")
        other = AuthContext(user_id="user-alpha", tenant_id="tenant-beta", role="member")
        run_id = str(uuid4())
        record = {"id": run_id, "question": "ws-alpha 429", "status": "queued"}
        repository.create(record, owner, knowledge=False, product_version=None)
        with self.assertRaises(LookupError):
            repository.load(run_id, other)

        async def verify():
            with self.assertRaises(LookupError):
                await execute_run(repository, None, run_id, other, SupportScenarioMock)
            from sqlalchemy import text

            with self.engine.begin() as connection:
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:id, 0))"), {"id": run_id}
                )
                with self.assertRaises(RunBusy):
                    await execute_run(repository, None, run_id, owner, SupportScenarioMock)
            self.assertEqual(repository.load(run_id, owner)["record"]["status"], "queued")

        asyncio.run(verify())

    def test_api_load_after_restart_and_terminal_resume(self):
        async def verify():
            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://test"
                ) as client,
            ):
                response = await client.post("/runs", json={"question": "ws-alpha 429"})
                self.assertEqual(response.status_code, 202)
                run_id = response.json()["id"]
                for _ in range(300):
                    saved = (await client.get(f"/runs/{run_id}")).json()
                    if saved["status"] == "completed":
                        break
                    await asyncio.sleep(0.02)
                self.assertEqual(saved["status"], "completed")
            pending = dict(
                saved,
                id=str(uuid4()),
                status="queued",
                reason="",
                answer="",
                events=[],
                state_path=[],
                model_calls=0,
                tool_calls=0,
            )
            owner = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")
            repository = RunRepository(self.engine)
            repository.create(pending, owner, knowledge=False, product_version=None)
            foreign_id = str(uuid4())
            repository.create(
                dict(pending, id=foreign_id),
                AuthContext(user_id="user-beta", tenant_id="tenant-beta", role="member"),
                knowledge=False,
                product_version=None,
            )

            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://test"
                ) as client,
            ):
                self.assertEqual((await client.get(f"/runs/{run_id}")).json(), saved)
                self.assertEqual((await client.get(f"/runs/{foreign_id}")).status_code, 404)
                self.assertEqual((await client.post(f"/runs/{foreign_id}/resume")).status_code, 404)
                self.assertEqual(
                    (await client.post(f"/runs/{pending['id']}/resume")).status_code, 202
                )
                self.assertEqual(
                    (await client.post(f"/runs/{pending['id']}/resume")).status_code, 409
                )
                for _ in range(300):
                    recovered = (await client.get(f"/runs/{pending['id']}")).json()
                    if recovered["status"] == "completed":
                        break
                    await asyncio.sleep(0.02)
                self.assertEqual(recovered["status"], "completed")

                self.assertEqual((await client.post(f"/runs/{run_id}/resume")).json(), saved)
                self.assertEqual((await client.post(f"/runs/{uuid4()}/resume")).status_code, 404)

        with patch.dict(
            os.environ, {"DATABASE_URL": self.connection_url(), "SUPPORTOPS_PERSIST_RUNS": "1"}
        ):
            asyncio.run(verify())
