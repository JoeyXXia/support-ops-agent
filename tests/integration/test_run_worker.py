"""Queue contracts with real PostgreSQL and a killed worker process."""

import asyncio
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx2
from sqlalchemy import text
from support import ROOT, PostgreSQLCase

from apps.api.main import app
from apps.api.sessions import COOKIE
from packages.agent.persistence import RunRepository
from packages.agent.run_queue import MAX_ATTEMPTS, RunQueue
from packages.tools.schemas import AuthContext

OWNER = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")
PAUSING_WORKER = """
import asyncio
from apps.api.runs import DemoProvider
from apps.api.worker import run_worker
from packages.agent.persistence import checkpoint_saver
from packages.agent.run_queue import RunQueue
from packages.storage.database import engine_from_environment

class Paused(DemoProvider):
    async def generate(self, request):
        print("model_started", flush=True)
        await asyncio.Future()

async def main():
    engine = engine_from_environment()
    async with checkpoint_saver(engine) as saver:
        await run_worker(RunQueue(engine), saver, lease_seconds=2, provider_factory=Paused)

asyncio.run(main())
"""


class WorkerDatabaseTests(PostgreSQLCase):
    def test_concurrent_claim_expiry_fencing_and_retry_limit(self):
        queue = RunQueue(self.engine)
        repo = RunRepository(self.engine)
        run_id = uuid4()
        record = {"id": str(run_id), "status": "queued"}
        repo.create(record, OWNER, knowledge=False, product_version=None, enqueue=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = list(pool.map(queue.claim, [30, 30]))
        self.assertEqual(sum(job is not None for job in jobs), 1)
        stale = next(job for job in jobs if job is not None)
        self.assertEqual(stale.context, OWNER)
        self.assertTrue(queue.heartbeat(stale, 30))
        self.assertIsNone(queue.claim(30))

        def expire():
            with self.engine.begin() as conn:
                conn.execute(
                    text("""UPDATE agent_runs
                            SET job_lease_until=clock_timestamp() - interval '1 second'
                            WHERE id=:id"""),
                    {"id": run_id},
                )

        for _ in range(MAX_ATTEMPTS - 1):
            expire()
            replacement = queue.claim(30)
            self.assertIsNotNone(replacement)
            self.assertNotEqual(replacement.lease_id, stale.lease_id)
            self.assertFalse(queue.heartbeat(stale, 30))
            self.assertFalse(queue.save(stale, record | {"status": "completed"}, finished=True))
        expire()
        self.assertIsNone(queue.claim(30))
        self.assertEqual(repo.load(run_id, OWNER)["record"]["reason"], "worker_retry_exhausted")

    def api_environment(self):
        url = self.url.update_query_dict({"options": f"-csearch_path={self.schema},public"})
        return {
            "DATABASE_URL": url.render_as_string(hide_password=False),
            "SUPPORTOPS_PERSIST_RUNS": "1",
            "SUPPORTOPS_DEMO_PASSWORDS": "",
            "SUPPORTOPS_READ_TOOL_TRANSPORT": "direct",
            "PYTHONUTF8": "1",
        }

    def client(self, *, raise_app_exceptions=True):
        return httpx2.AsyncClient(
            cookies={COOKIE: app.state.sessions.issue("user-alpha")},
            transport=httpx2.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
            base_url="http://test",
        )

    def test_approval_and_enqueue_share_transaction(self):
        async def verify():
            async def settle(run_id):
                for _ in range(250):
                    record = (await http.get(f"/runs/{run_id}")).json()
                    if record["status"] not in {"queued", "running"}:
                        return record
                    await asyncio.sleep(0.02)
                self.fail("run did not settle")

            async with self.serving(app), self.client(raise_app_exceptions=False) as http:
                created = await http.post(
                    "/runs", json={"question": "ws-alpha 429", "ticket_draft": True}
                )
                self.assertEqual(created.status_code, 202)
                run_id = created.json()["id"]
                draft = await settle(run_id)
                self.assertEqual(draft["status"], "awaiting_approval")
                proposal = draft["proposed_action"]
                path = f"/approvals/{proposal['approval_id']}"
                payload = {"run_id": run_id, "payload_digest": proposal["payload_digest"]}
                with patch.object(RunQueue, "enqueue", side_effect=RuntimeError("test rollback")):
                    failed = await http.post(path + "/approve", json=payload)
                self.assertEqual(failed.status_code, 500)
                self.assertEqual((await http.get(path)).json()["status"], "pending")
                self.assertEqual((await settle(run_id))["status"], "awaiting_approval")
                self.assertEqual(
                    (await http.post(path + "/approve", json=payload)).status_code, 202
                )
                self.assertEqual((await settle(run_id))["reason"], "ticket_created")
                with self.engine.connect() as conn:
                    self.assertEqual(conn.scalar(text("SELECT count(*) FROM tickets")), 1)
                    self.assertEqual(conn.scalar(text("SELECT count(*) FROM action_executions")), 1)

        with patch.dict(os.environ, self.api_environment()):
            asyncio.run(verify())

    def test_submit_then_api_restart_and_worker_process_restart(self):
        def state(run_id):
            with self.engine.connect() as conn:
                return conn.execute(
                    text("""SELECT job_pending, job_attempts, job_lease_id, job_lease_until
                            FROM agent_runs WHERE id=:id"""),
                    {"id": UUID(run_id)},
                ).one()

        async def wait_for(check, child, log):
            for _ in range(600):
                if check():
                    return
                if child.poll() is not None:
                    break
                await asyncio.sleep(0.05)
            self.fail(log.read_text(encoding="utf-8"))

        async def verify(directory):
            async with app.router.lifespan_context(app), self.client() as http:
                response = await http.post("/runs", json={"question": "ws-alpha 429"})
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.json()["status"], "queued")
                run_id = response.json()["id"]
            async with app.router.lifespan_context(app), self.client() as http:
                path = f"/runs/{run_id}"
                self.assertEqual((await http.get(path)).json()["status"], "queued")
                self.assertEqual(tuple(state(run_id)), (True, 0, None, None))

                async def launch(args, name, check):
                    log = Path(directory) / name
                    with log.open("w", encoding="utf-8") as stream:
                        child = subprocess.Popen(
                            [sys.executable, *args],
                            cwd=ROOT,
                            env=os.environ.copy(),
                            stdout=stream,
                            stderr=subprocess.STDOUT,
                        )
                        try:
                            await wait_for(lambda: check(log), child, log)
                        finally:
                            if child.poll() is None:
                                child.kill()
                            child.wait(timeout=10)

                first_lease = None

                def renewed(log):
                    nonlocal first_lease
                    if "model_started" not in log.read_text(encoding="utf-8"):
                        return False
                    until = state(run_id).job_lease_until
                    if first_lease is None:
                        first_lease = until
                    return until > first_lease

                await launch(["-c", PAUSING_WORKER], "first.log", renewed)
                self.assertEqual(state(run_id).job_attempts, 1)
                interrupted = (await http.get(path)).json()
                self.assertEqual(interrupted["status"], "running")
                self.assertEqual(
                    [event["kind"] for event in interrupted["events"]], ["step.started"]
                )
                await launch(
                    ["-m", "apps.api.worker"], "second.log", lambda _: not state(run_id).job_pending
                )
                saved = (await http.get(path)).json()
                self.assertEqual(saved["id"], run_id)
                self.assertEqual(
                    (saved["status"], saved["model_calls"], saved["tool_calls"]),
                    ("completed", 2, 2),
                )
                self.assertEqual(len(saved["events"]), 8)
                self.assertEqual(tuple(state(run_id)), (False, 2, None, None))

        with TemporaryDirectory() as directory, patch.dict(os.environ, self.api_environment()):
            asyncio.run(verify(directory))
