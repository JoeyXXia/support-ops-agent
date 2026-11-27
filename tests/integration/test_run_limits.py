"""Real PostgreSQL coverage for Day37 capacity and cooperative stop."""

import asyncio
import os
from unittest.mock import patch

from support import PostgreSQLCase

from apps.api.main import app
from apps.api.sessions import COOKIE
from apps.api.worker import running_worker
from packages.agent.models import ModelResponse
from packages.tools.executor import ToolExecutor


class QueueControlTests(PostgreSQLCase):
    async def submit(self, http, question="ws-alpha 429", **settings):
        return await http.post("/runs", json={"question": question, **settings})

    async def settled(self, http, run_id):
        async with asyncio.timeout(15):
            while True:
                row = (await http.get(f"/runs/{run_id}")).json()
                if row["status"] not in {"queued", "running"}:
                    return row
                await asyncio.sleep(0.02)

    def test_capacity_and_queued_cancel(self):
        async def verify():
            async with app.router.lifespan_context(app), self.client(app) as http:

                async def other(actor):
                    return await http.post(
                        "/runs",
                        json={"question": "ws-alpha 429"},
                        headers={"Cookie": f"{COOKIE}={app.state.sessions.issue(actor)}"},
                    )

                first = await self.submit(http)
                self.assertEqual(first.status_code, 202)
                self.assertEqual((await self.submit(http)).status_code, 429)
                self.assertEqual((await other("user-beta")).status_code, 202)
                self.assertEqual((await other("support-alpha")).status_code, 503)
                run_id = first.json()["id"]
                path = f"/runs/{run_id}/cancel"
                foreign = f"{COOKIE}={app.state.sessions.issue('user-beta')}"
                self.assertEqual(
                    (await http.post(path, headers={"Cookie": foreign})).status_code, 404
                )
                self.assertEqual((await http.post(path)).json()["status"], "cancelled")
                self.assertEqual(
                    (await http.post(f"/runs/{run_id}/resume")).json()["status"], "cancelled"
                )
                self.assertEqual((await other("support-alpha")).status_code, 202)

        with patch.dict(
            os.environ,
            self.api_environment()
            | {
                "SUPPORTOPS_USER_RUN_LIMIT": "1",
                "SUPPORTOPS_QUEUE_LIMIT": "2",
            },
        ):
            asyncio.run(verify())

    def test_worker_concurrency_bound(self):
        async def verify():
            release = asyncio.Event()
            two_started = asyncio.Event()
            started = 0

            class Provider:
                async def generate(self, request):
                    nonlocal started
                    started += 1
                    if started == 2:
                        two_started.set()
                    await release.wait()
                    return ModelResponse(mode="mock", model="test", text="done")

            from apps.api.worker import DemoProvider

            with patch.object(DemoProvider, "generate", Provider.generate):
                async with self.serving(app), self.client(app) as http:
                    runs = [(await self.submit(http)).json()["id"] for _ in range(3)]
                    await asyncio.wait_for(two_started.wait(), 5)
                    await asyncio.sleep(0.3)
                    self.assertEqual(started, 2)
                    release.set()
                    for run_id in runs:
                        self.assertEqual((await self.settled(http, run_id))["status"], "completed")
                    self.assertEqual(started, 3)

        with patch.dict(
            os.environ, self.api_environment() | {"SUPPORTOPS_WORKER_CONCURRENCY": "2"}
        ):
            asyncio.run(verify())

    def test_cancel_or_timeout_during_tool_stops_next_operation(self):
        async def verify(timeout):
            release = asyncio.Event()
            started = asyncio.Event()
            tools = []
            original = ToolExecutor.execute_async

            async def execute(executor, name, arguments, *, context):
                tools.append(name)
                started.set()
                await release.wait()
                return await original(executor, name, arguments, context=context)

            with patch.object(ToolExecutor, "execute_async", execute):
                async with self.serving(app), self.client(app) as http:
                    run_id = (await self.submit(http)).json()["id"]
                    await asyncio.wait_for(started.wait(), 5)
                    if timeout:
                        with self.engine.begin() as conn:
                            from sqlalchemy import text

                            conn.execute(
                                text("""UPDATE agent_runs
                                SET job_deadline=clock_timestamp() - interval '1 second'
                                WHERE id=:id"""),
                                {"id": run_id},
                            )
                    else:
                        response = await http.post(f"/runs/{run_id}/cancel")
                        self.assertTrue(response.json()["cancel_requested"])
                    release.set()
                    final = await self.settled(http, run_id)
                    self.assertEqual(
                        (final["status"], final["reason"]),
                        ("failed", "run_timeout")
                        if timeout
                        else ("cancelled", "cancelled_by_owner"),
                    )
                    self.assertEqual(tools, ["get_workspace_usage"])
                    self.assertIn("tool.finished", [event["kind"] for event in final["events"]])

        with patch.dict(os.environ, self.api_environment()):
            for timeout in (False, True):
                with self.subTest(timeout=timeout):
                    asyncio.run(verify(timeout))

    def test_cancel_preserves_committed_ticket_and_blocks_new_write(self):
        async def verify():
            async with app.router.lifespan_context(app), self.client(app) as http:
                engine = app.state.runs.repository.engine
                async with running_worker(engine):
                    drafts = []
                    for _ in range(2):
                        run = (await self.submit(http, ticket_draft=True)).json()
                        drafts.append(await self.settled(http, run["id"]))
                for index, row in enumerate(drafts):
                    proposal = row["proposed_action"]
                    payload = {"run_id": row["id"], "payload_digest": proposal["payload_digest"]}
                    approval = f"/approvals/{proposal['approval_id']}/approve"
                    self.assertEqual((await http.post(approval, json=payload)).status_code, 202)
                    ticket_input = {"approval_id": proposal["approval_id"]}
                    if index == 0:
                        write = await http.post("/tickets", json=ticket_input)
                        self.assertEqual(write.status_code, 201)
                    cancelled = (await http.post(f"/runs/{row['id']}/cancel")).json()
                    self.assertEqual(cancelled["status"], "cancelled")
                    retry = await http.post("/tickets", json=ticket_input)
                    if index == 0:
                        self.assertEqual(cancelled["ticket"], write.json())
                        self.assertEqual(retry.json(), write.json())
                    else:
                        self.assertFalse(cancelled.get("ticket"))
                        self.assertEqual(retry.status_code, 409)
                        self.assertEqual((await http.post(approval, json=payload)).status_code, 409)

        with patch.dict(os.environ, self.api_environment()):
            asyncio.run(verify())
