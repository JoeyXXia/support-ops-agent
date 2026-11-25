import asyncio
import os
import unittest
from unittest.mock import patch

import httpx2

from apps.api.main import app
from packages.agent.conversation import ConversationStore
from packages.tools.schemas import AuthContext


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_fifteen_turns_remain_bounded_and_foreign_actor_gets_no_context(self):
        with patch.dict(
            os.environ, {"SUPPORTOPS_PERSIST_RUNS": "0", "SUPPORTOPS_DEMO_PASSWORDS": ""}
        ):
            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://test"
                ) as client,
            ):
                alpha = {"Cookie": "supportops_session=" + app.state.sessions.issue("user-alpha")}
                beta = {"Cookie": "supportops_session=" + app.state.sessions.issue("user-beta")}
                created = await client.post("/conversations", json={}, headers=alpha)
                self.assertEqual(created.status_code, 201)
                cid = created.json()["id"]
                uri = f"/conversations/{cid}"
                body = {"conversation_id": cid, "knowledge": True}
                for turn in range(1, 16):
                    submitted = await client.post(
                        "/runs", json=dict(body, question=f"轮次 {turn}"), headers=alpha
                    )
                    self.assertEqual(submitted.status_code, 202)
                    run_id = submitted.json()["id"]
                    for _ in range(100):
                        result = (await client.get(f"/runs/{run_id}", headers=alpha)).json()
                        if result["status"] not in {"queued", "running"}:
                            break
                        await asyncio.sleep(0.01)
                    self.assertEqual(
                        (result["status"], result["reason"]), ("completed", "clarification")
                    )
                response = await client.get(uri, headers=alpha)
                self.assertEqual(response.headers["cache-control"], "no-store")
                memory = response.json()
                self.assertEqual(memory["turn_count"], 15)
                self.assertEqual(
                    [(x["turn"], x["question"]) for x in memory["recent_turns"]],
                    [(n, f"轮次 {n}") for n in range(12, 16)],
                )
                self.assertEqual(len(memory["recent_turns"]), 4)
                self.assertTrue(all(x["run_id"] for x in memory["recent_turns"]))
                self.assertLessEqual(memory["summary_utf8_bytes"], memory["summary_budget_bytes"])
                self.assertEqual(memory["facts"], {})
                support = {
                    "Cookie": "supportops_session=" + app.state.sessions.issue("support-alpha")
                }
                self.assertEqual((await client.get(uri, headers=support)).status_code, 404)
                self.assertEqual((await client.get(uri, headers=beta)).status_code, 404)
                self.assertEqual(
                    (
                        await client.patch(uri, json={"product_version": "2.0"}, headers=beta)
                    ).status_code,
                    404,
                )
                self.assertEqual(
                    (
                        await client.post(
                            "/runs", json=dict(body, question="foreign"), headers=beta
                        )
                    ).status_code,
                    404,
                )
                self.assertEqual((await client.get(uri)).status_code, 401)

                changed = await client.patch(uri, json={"product_version": "1.0"}, headers=alpha)
                self.assertEqual(
                    changed.json()["facts"]["product_version"],
                    {"revision": 1, "source": "explicit_user_update", "value": "1.0"},
                )
                self.assertEqual(changed.json()["summary"], "product_version=1.0")
                self.assertEqual(
                    (
                        await client.post(
                            "/runs",
                            json=dict(body, question="conflict", product_version="2.0"),
                            headers=alpha,
                        )
                    ).status_code,
                    422,
                )
                self.assertEqual(
                    (
                        await client.patch(
                            uri,
                            json={"product_version": "2.0", "tenant_id": "tenant-beta"},
                            headers=alpha,
                        )
                    ).status_code,
                    422,
                )
                self.assertEqual(
                    (
                        await client.put(
                            "/preferences",
                            json={"product_version": "1.0", "confirmed": True},
                            headers=alpha,
                        )
                    ).status_code,
                    409,
                )

    def test_capacity_expiration_and_full_context_match(self):
        tick = [1]
        store = ConversationStore(clock=lambda: tick[0], ttl=10, capacity=1)
        owner = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")
        other = owner.model_copy(update={"tenant_id": "tenant-beta"})
        first, _ = store.create(owner)
        with self.assertRaises(LookupError):
            store.get(first, other)
        with self.assertRaises(OverflowError):
            store.create(owner)
        tick[0] = 11
        with self.assertRaises(LookupError):
            store.get(first, owner)
        self.assertNotEqual(store.create(owner)[0], first)
