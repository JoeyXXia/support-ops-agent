import asyncio
import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx2

from apps.api.events import Projection, stream_run
from apps.api.main import app
from apps.api.runs import RunStore


def decode(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


class EventTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(
            os.environ, {"SUPPORTOPS_PERSIST_RUNS": "0", "SUPPORTOPS_DEMO_PASSWORDS": ""}
        )
        self.env.start()
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        self.client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://test",
            cookies={"supportops_session": app.state.sessions.issue("user-alpha")},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.env.stop()

    async def test_success_sequence_is_deterministic_and_framed(self):
        expected = [
            "run.snapshot",
            "run.started",
            "step.started",
            "model.finished",
            "tool.started",
            "tool.finished",
            "tool.started",
            "tool.finished",
            "step.started",
            "model.finished",
            "answer.delta",
            "run.finished",
        ]
        for _ in range(2):
            created = (await self.client.post("/runs", json={"question": "ws-alpha 429"})).json()
            response = await self.client.get(f"/runs/{created['id']}/events")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
            events = decode(response.text)
            self.assertEqual([e["type"] for e in events], expected)
            self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
            self.assertTrue(all(e["run_id"] == created["id"] for e in events))
            self.assertEqual(events[-1]["data"]["run"]["status"], "completed")
            self.assertEqual(events[-2]["data"]["text"], events[-1]["data"]["run"]["answer"])
            self.assertEqual(app.state.runs.active_streams, 0)

    async def test_anonymous_foreign_and_cursor_requests_rejected(self):
        run = (await self.client.post("/runs", json={"question": "ws-alpha 429"})).json()
        path = f"/runs/{run['id']}/events"
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test"
        ) as anonymous:
            self.assertEqual((await anonymous.get(path)).status_code, 401)
        self.assertEqual(
            (
                await self.client.get(
                    path,
                    headers={
                        "Cookie": "supportops_session=" + app.state.sessions.issue("user-beta")
                    },
                )
            ).status_code,
            404,
        )
        self.assertEqual(
            (await self.client.get(path, headers={"Last-Event-ID": "4"})).status_code, 409
        )
        app.state.runs.active_streams = 4
        self.assertEqual((await self.client.get(path)).status_code, 429)
        app.state.runs.active_streams = 0
        self.assertEqual((await self.client.get(f"/runs/{uuid4()}/events")).status_code, 404)

    async def test_runtime_failure_has_terminal_event_and_no_exception_text(self):
        class Failure:
            async def generate(self, request):
                raise RuntimeError("synthetic-private-exception")

        app.state.runs = RunStore(provider_factory=Failure)
        record = (await self.client.post("/runs", json={"question": "ws-alpha 429"})).json()
        response = await self.client.get(f"/runs/{record['id']}/events")
        events = decode(response.text)
        self.assertEqual(events[-1]["type"], "run.finished")
        self.assertIn(events[-1]["data"]["run"]["status"], {"failed", "stopped"})
        self.assertNotIn("synthetic-private-exception", response.text)

    async def test_disconnect_does_not_cancel_run_and_revocation_stops_next_delivery(self):
        record = app.state.runs.create("ws-alpha 429")
        stream = stream_run(app.state.runs, record["id"], lambda: None, interval=0.001)
        self.assertEqual(decode(await anext(stream))[0]["type"], "run.snapshot")
        await stream.aclose()
        self.assertTrue(app.state.runs.tasks)
        await asyncio.gather(*list(app.state.runs.tasks))
        self.assertEqual(app.state.runs.get(record["id"])["status"], "completed")
        token = app.state.sessions.issue("user-alpha")
        stream = stream_run(app.state.runs, record["id"], lambda: app.state.sessions.resolve(token))
        await anext(stream)
        app.state.sessions.revoke(token)
        # The next read must recheck authorization, even when more frames were in the snapshot.
        # Revocation between frames is checked by the stream, not just before HTTP headers.
        self.assertEqual(decode(await anext(stream))[0]["data"]["code"], "unauthorized")
        await stream.aclose()

    async def test_timeout_and_storage_failure_are_stream_errors(self):
        record = app.state.runs.create("ws-alpha 429")
        stream = stream_run(app.state.runs, record["id"], lambda: None, timeout=0)
        self.assertEqual(decode(await anext(stream))[0]["data"]["code"], "stream_timeout")
        await stream.aclose()
        with patch.object(app.state.runs, "get", side_effect=RuntimeError("synthetic DB secret")):
            stream = stream_run(app.state.runs, record["id"], lambda: None)
            frame = await anext(stream)
            self.assertEqual(decode(frame)[0]["data"]["code"], "stream_unavailable")
            self.assertNotIn("synthetic DB secret", frame)
            await stream.aclose()

    async def test_projection_approval_and_public_field_allowlist(self):
        record = {
            "id": "example",
            "status": "awaiting_approval",
            "answer": "草稿已生成",
            "internal_reasoning": "never publish internal bytes",
            "events": [
                {"kind": "model.finished", "step": 1, "raw_output": "never publish internal bytes"},
                {"kind": "internal.private", "step": 1, "value": "never publish internal bytes"},
            ],
        }
        first = "".join(Projection("example").frames(record))
        second = "".join(Projection("example").frames(record))
        self.assertEqual(first, second)
        self.assertEqual(decode(first)[-1]["type"], "approval.required")
        self.assertNotIn("run.finished", [e["type"] for e in decode(first)])
        self.assertNotIn("never publish internal bytes", first)
