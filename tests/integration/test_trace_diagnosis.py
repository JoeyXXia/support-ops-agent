"""Trace a synthetic tool failure and a failed worker response after a real commit."""

import asyncio
import json
import os
from time import perf_counter
from unittest.mock import patch
from uuid import UUID

from support import PostgreSQLCase

from apps.api.main import app
from apps.api.sessions import COOKIE
from packages.storage.tickets import TicketRepository
from packages.tools.executor import ToolExecutor
from packages.tools.schemas import ToolFailure, ToolResult

MARKER = "synthetic-private-trace-marker"


class TraceDiagnosisTests(PostgreSQLCase):
    async def settled(self, http, run_id):
        async with asyncio.timeout(15):
            while True:
                record = (await http.get(f"/runs/{run_id}")).json()
                if record["status"] not in {"running", "queued"}:
                    return record
                await asyncio.sleep(0.02)

    async def diagnose(self, http, run_id):
        started = perf_counter()
        result = await http.get(f"/runs/{run_id}/trace")
        self.assertEqual(result.status_code, 200)
        self.assertNotIn(MARKER, result.text)
        seconds = perf_counter() - started
        self.assertLess(seconds, 300)
        print(f"diagnosis_lookup_seconds={seconds:.3f}")
        return result.json()

    def assert_log_fields(self, captured, run_id, request_id, operations):
        rows = [json.loads(item.getMessage()) for item in captured.records]
        self.assertNotIn(MARKER, json.dumps(rows))
        relevant = [row for row in rows if row["run_id"] == run_id]
        self.assertTrue(operations.issubset({row["operation"] for row in relevant}))
        self.assertTrue(all(row["duration_ms"] >= 0 for row in relevant))
        for row in relevant:
            if row["operation"] in {"model", "retrieval", "tool", "write"}:
                self.assertEqual(row["request_id"], request_id)
                self.assertTrue(row["step_id"].startswith(f"{run_id}:"))

    def test_tool_failure_reports_step_and_no_committed_write(self):
        async def fail(executor, name, arguments, *, context):
            return ToolResult(
                tool=name, ok=False, error=ToolFailure(code="not_found", message=MARKER)
            )

        async def verify():
            async with self.serving(app), self.client(app) as http:
                with self.assertLogs("supportops.trace", level="INFO") as captured:
                    with patch.object(ToolExecutor, "execute_async", fail):
                        created = await http.post(
                            "/runs",
                            json={"question": f"ws-alpha 429 {MARKER}"},
                            headers={"X-Request-ID": MARKER},
                        )
                        request_id = created.headers["x-request-id"]
                        UUID(request_id)
                        record = created.json()
                        run_id = record["id"]
                        self.assertEqual(record["request_id"], request_id)
                        self.assertEqual((await self.settled(http, run_id))["status"], "failed")
                    diagnosis = await self.diagnose(http, run_id)
                    foreign = f"{COOKIE}={app.state.sessions.issue('user-beta')}"
                    response = await http.get(f"/runs/{run_id}/trace", headers={"Cookie": foreign})
                    self.assertEqual(response.status_code, 404)
                self.assertEqual(
                    diagnosis["failure"],
                    {
                        "error_code": "not_found",
                        "step": 1,
                        "step_id": f"{run_id}:1",
                        "operation": "tool",
                    },
                )
                self.assertEqual(
                    diagnosis["write"], {"draft_saved": False, "ticket_receipt": "no_receipt"}
                )
                self.assertEqual(diagnosis["request_id"], request_id)
                self.assert_log_fields(
                    captured,
                    run_id,
                    request_id,
                    {
                        "database.run_enqueue",
                        "database.checkpoint_read",
                        "database.run_save",
                        "database.trace_receipt",
                        "model",
                        "tool",
                    },
                )

        with patch.dict(os.environ, self.api_environment()):
            asyncio.run(verify())

    def test_retrieval_and_approval_timing_and_failure_after_commit(self):
        original = TicketRepository.create_ticket

        def commit_then_fail(repo, *args, **kwargs):
            original(repo, *args, **kwargs)
            raise RuntimeError(MARKER)

        async def verify():
            async with self.serving(app), self.client(app) as http:
                with self.assertLogs("supportops.trace", level="INFO") as captured:
                    request = await http.post(
                        "/runs",
                        json={
                            "question": "starter配额是多少？",
                            "product_version": "2.0",
                            "knowledge": True,
                        },
                    )
                    retrieval_id = request.json()["id"]
                    knowledge = await self.settled(http, retrieval_id)
                    self.assertTrue(
                        any(
                            event.get("duration_ms") is not None
                            for event in knowledge["events"]
                            if event.get("tool") == "search_docs"
                        )
                    )
                    created = await http.post(
                        "/runs", json={"question": MARKER, "ticket_draft": True}
                    )
                    run_id = created.json()["id"]
                    draft = await self.settled(http, run_id)
                    action = draft["proposed_action"]
                    with patch.object(TicketRepository, "create_ticket", commit_then_fail):
                        approved = await http.post(
                            f"/approvals/{action['approval_id']}/approve",
                            json={"run_id": run_id, "payload_digest": action["payload_digest"]},
                        )
                        self.assertEqual(approved.status_code, 202)
                        final = await self.settled(http, run_id)
                    diagnosis = await self.diagnose(http, run_id)
                self.assertEqual(final["status"], "failed")
                self.assertIsNone(final["ticket"])
                self.assertEqual(diagnosis["failure"]["operation"], "write")
                self.assertEqual(diagnosis["failure"]["error_code"], "internal_error")
                self.assertEqual(
                    diagnosis["write"], {"draft_saved": True, "ticket_receipt": "committed"}
                )
                self.assert_log_fields(
                    captured, run_id, created.headers["x-request-id"], {"approval", "write"}
                )
                self.assert_log_fields(
                    captured, retrieval_id, request.headers["x-request-id"], {"retrieval"}
                )

        with patch.dict(os.environ, self.api_environment()):
            asyncio.run(verify())
