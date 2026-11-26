"""Owner-scoped memory behavior against real PostgreSQL and the existing mock graph."""

import asyncio
import os
from unittest.mock import patch

import httpx2
from support import PostgreSQLCase

from apps.api.main import app
from packages.knowledge.embeddings import MockEmbedding
from packages.knowledge.ingestion import ingest_version
from packages.knowledge.repository import import_batch


class PersistentMemoryTests(PostgreSQLCase):
    def test_confirm_correct_retract_delete_and_restart(self):
        item = next(doc for doc in self.items if doc.document_key == "quotas")
        version = import_batch(self.engine, [item], tenant_id=None)[0]["id"]
        self.assertEqual(ingest_version(self.engine, version, MockEmbedding())["status"], "ready")

        def header(subject):
            return {"Cookie": "supportops_session=" + app.state.sessions.issue(subject)}

        async def settled(client, run_id, headers):
            for _ in range(150):
                reply = await client.get(f"/runs/{run_id}", headers=headers)
                self.assertEqual(reply.status_code, 200)
                record = reply.json()
                if record["status"] not in ("queued", "running"):
                    self.assertEqual(record["status"], "completed", record)
                    return record
                await asyncio.sleep(0.02)
            self.fail("Knowledge run did not settle")

        async def scenario():
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url="http://test"
            ) as client:
                async with self.serving(app):
                    alpha, beta = header("user-alpha"), header("user-beta")
                    for body in (
                        {"product_version": "1.0"},
                        {"product_version": "1.0", "confirmed": False},
                        {"product_version": "1.0", "confirmed": 1},
                    ):
                        self.assertEqual(
                            (
                                await client.put("/preferences", json=body, headers=alpha)
                            ).status_code,
                            422,
                        )
                    self.assertIsNone(
                        (await client.get("/preferences", headers=alpha)).json()["product_version"]
                    )
                    for headers, value in ((alpha, "1.0"), (beta, "2.0")):
                        saved = await client.put(
                            "/preferences",
                            json={"product_version": value, "confirmed": True},
                            headers=headers,
                        )
                        self.assertEqual(
                            (saved.status_code, saved.json()["product_version"]), (200, value)
                        )
                    blank = await client.post("/conversations", json={}, headers=alpha)
                    self.assertEqual(blank.json()["facts"], {})
                    conversation = await client.post(
                        "/conversations", json={"use_preference": True}, headers=alpha
                    )
                    cid = conversation.json()["id"]
                    self.assertEqual(
                        conversation.json()["facts"]["product_version"]["source"],
                        "confirmed_preference",
                    )
                    uri = f"/conversations/{cid}"
                    payload = {
                        "conversation_id": cid,
                        "knowledge": True,
                        "question": "starter配额是多少？",
                    }
                    first = await client.post("/runs", json=payload, headers=alpha)
                    patched = await client.patch(
                        uri, json={"product_version": "2.0"}, headers=alpha
                    )
                    self.assertEqual(patched.json()["summary"], "product_version=2.0")
                    self.assertEqual(patched.json()["facts"]["product_version"]["revision"], 2)
                    second = await client.post("/runs", json=payload, headers=alpha)
                    for reply, expected in ((first, "1.0"), (second, "2.0")):
                        self.assertEqual(reply.status_code, 202)
                        run = await settled(client, reply.json()["id"], alpha)
                        self.assertEqual(
                            {row["product_version"] for row in run["citations"]}, {expected}
                        )
                    withdrawn = await client.patch(
                        uri, json={"product_version": None}, headers=alpha
                    )
                    self.assertEqual(
                        (withdrawn.json()["facts"], withdrawn.json()["summary"]), ({}, "")
                    )
                    reply = await client.post("/runs", json=payload, headers=alpha)
                    run = await settled(client, reply.json()["id"], alpha)
                    self.assertEqual((run["reason"], run["tool_calls"]), ("clarification", 0))

                async with self.serving(app):
                    alpha, beta = header("user-alpha"), header("user-beta")
                    self.assertEqual((await client.get(uri, headers=alpha)).status_code, 404)
                    self.assertEqual(
                        (await client.get("/preferences", headers=alpha)).json()["product_version"],
                        "1.0",
                    )
                    auto = (
                        await client.post(
                            "/conversations", json={"use_preference": True}, headers=alpha
                        )
                    ).json()
                    manual = (await client.post("/conversations", json={}, headers=alpha)).json()
                    await client.patch(
                        f"/conversations/{manual['id']}",
                        json={"product_version": "2.0"},
                        headers=alpha,
                    )
                    self.assertEqual(
                        (await client.delete("/preferences", headers=alpha)).status_code, 204
                    )
                    self.assertEqual(
                        (await client.get(f"/conversations/{auto['id']}", headers=alpha)).json()[
                            "facts"
                        ],
                        {},
                    )
                    snapshot = (
                        await client.get(f"/conversations/{manual['id']}", headers=alpha)
                    ).json()
                    self.assertEqual(snapshot["facts"]["product_version"]["value"], "2.0")
                    later = await client.post(
                        "/conversations", json={"use_preference": True}, headers=alpha
                    )
                    self.assertEqual(later.json()["facts"], {})
                    self.assertIsNone(
                        (await client.get("/preferences", headers=alpha)).json()["product_version"]
                    )
                    self.assertEqual(
                        (await client.get("/preferences", headers=beta)).json()["product_version"],
                        "2.0",
                    )

        url = self.url.update_query_dict({"options": f"-csearch_path={self.schema},public"})
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": url.render_as_string(hide_password=False),
                "SUPPORTOPS_PERSIST_RUNS": "1",
                "SUPPORTOPS_READ_TOOL_TRANSPORT": "direct",
                "SUPPORTOPS_DEMO_PASSWORDS": "",
            },
        ):
            asyncio.run(scenario())
