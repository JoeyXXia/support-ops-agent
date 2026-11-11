import asyncio
import unittest
from uuid import uuid4

import httpx2

from apps.api.main import app
from apps.api.runs import RunStore
from packages.agent.models import ProviderError


class RunApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        self.client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)

    async def wait(self, run_id):
        for _ in range(200):
            record = (await self.client.get(f"/runs/{run_id}")).json()
            if record["status"] not in {"queued", "running"}:
                return record
            await asyncio.sleep(0.01)
        self.fail("run did not terminate")

    async def test_accept_poll_and_answer(self):
        response = await self.client.post("/runs", json={"question": "ws-alpha 429"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "queued")
        record = await self.wait(response.json()["id"])
        self.assertEqual(
            (record["status"], record["mode"], record["tool_calls"]), ("completed", "mock", 2)
        )
        self.assertIn("60/60", record["answer"])
        self.assertEqual(len(record["events"]), 4)
        self.assertEqual(
            record["state_path"], ["classify", "call_tool", "classify", "answer", "done"]
        )

    async def test_partial_events_visible(self):
        created = (await self.client.post("/runs", json={"question": "ws-alpha 429"})).json()
        await asyncio.sleep(0.4)
        record = (await self.client.get(f"/runs/{created['id']}")).json()
        self.assertEqual(record["status"], "running")
        self.assertTrue(record["events"])

    async def test_validation_and_identity_rejection(self):
        for body in (
            {"question": " "},
            {"question": "x" * 2001},
            {"question": 3},
            {"question": "ws-alpha 429", "tenant_id": "tenant-beta"},
        ):
            self.assertEqual((await self.client.post("/runs", json=body)).status_code, 422)
        self.assertEqual(len(app.state.runs.records), 0)

    async def test_missing_and_invalid_id(self):
        self.assertEqual((await self.client.get(f"/runs/{uuid4()}")).status_code, 404)
        self.assertEqual((await self.client.get("/runs/not-a-uuid")).status_code, 422)

    async def test_admission_limit(self):
        app.state.runs.max_active = 1
        await self.client.post("/runs", json={"question": "ws-alpha 429"})
        self.assertEqual(
            (await self.client.post("/runs", json={"question": "ws-alpha 429"})).status_code, 503
        )

    async def test_failure_is_terminal_and_redacted(self):
        class Failure:
            async def generate(self, request):
                raise ProviderError("timeout")

        app.state.runs.provider_factory = Failure
        created = (await self.client.post("/runs", json={"question": "ws-alpha 429"})).json()
        record = await self.wait(created["id"])
        self.assertEqual((record["status"], record["reason"]), ("failed", "timeout"))
        self.assertEqual(record["answer"], "")
        self.assertEqual(record["state_path"], ["classify", "done"])

    async def test_clarify_other_question(self):
        created = (await self.client.post("/runs", json={"question": "ws-beta 429"})).json()
        record = await self.wait(created["id"])
        self.assertEqual(record["tool_calls"], 0)
        self.assertIn("仅支持", record["answer"])

    async def test_grounded_clarification_route(self):
        created = (
            await self.client.post(
                "/runs", json={"question": "starter配额是多少？", "knowledge": True}
            )
        ).json()
        record = await self.wait(created["id"])
        self.assertEqual((record["status"], record["reason"]), ("completed", "clarification"))
        self.assertEqual(record["state_path"], ["classify", "answer", "clarify", "done"])
        self.assertEqual(record["tool_calls"], 0)

    async def test_eviction_and_shutdown(self):
        store = RunStore(max_records=1)
        first = store.create("other question")
        await asyncio.gather(*list(store.tasks))
        second = store.create("ws-alpha 429")
        self.assertNotIn(first["id"], store.records)
        await store.close()
        self.assertEqual(store.records[second["id"]]["status"], "failed")
