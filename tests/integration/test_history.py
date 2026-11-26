import asyncio
import os
from unittest.mock import patch
from uuid import uuid4

import httpx2
from support import PostgreSQLCase

from apps.api.main import app
from packages.agent.persistence import RunRepository
from packages.knowledge.embeddings import MockEmbedding
from packages.knowledge.ingestion import ingest_version
from packages.knowledge.repository import import_batch
from packages.tools.schemas import AuthContext


class HistoryDatabaseTests(PostgreSQLCase):
    def environment(self):
        url = self.url.update_query_dict({"options": f"-csearch_path={self.schema},public"})
        return patch.dict(
            os.environ,
            {
                "DATABASE_URL": url.render_as_string(hide_password=False),
                "SUPPORTOPS_PERSIST_RUNS": "1",
                "SUPPORTOPS_DEMO_PASSWORDS": "",
            },
        )

    def test_owner_filter_and_history_survive_new_store(self):
        repository = RunRepository(self.engine)
        owned = []
        for user, tenant, role, created in (
            ("user-alpha", "tenant-alpha", "member", "2026-01-01T00:00:00+00:00"),
            ("user-alpha", "tenant-alpha", "member", "2026-01-02T00:00:00+00:00"),
            ("user-alpha", "tenant-alpha", "member", None),
            ("user-beta", "tenant-beta", "member", None),
            ("support-alpha", "tenant-alpha", "support", None),
        ):
            run_id = str(uuid4())
            context = AuthContext(
                user_id=user,
                tenant_id=tenant,
                role=role,
                assigned_tenant_ids=(tenant,) if role == "support" else (),
            )
            record = {
                "id": run_id,
                "question": "synthetic history fixture",
                "status": "completed",
                "reason": "answer",
                "answer": "synthetic private answer",
                "events": [],
                "citations": [],
            }
            if created:
                record["created_at"] = created
            repository.create(record, context, knowledge=False, product_version=None)
            if user == "user-alpha":
                owned.append(run_id)

        async def verify():
            for _ in range(2):
                async with (
                    self.serving(app),
                    httpx2.AsyncClient(
                        transport=httpx2.ASGITransport(app=app),
                        base_url="http://test",
                        cookies={"supportops_session": app.state.sessions.issue("user-alpha")},
                    ) as client,
                ):
                    response = await client.get("/runs")
                    self.assertEqual(response.json()["storage"], "postgresql")
                    rows = response.json()["runs"]
                    self.assertEqual([r["id"] for r in rows], [owned[1], owned[0], owned[2]])
                    self.assertIsNone(rows[-1]["created_at"])
                    self.assertTrue(all("answer" not in r and "events" not in r for r in rows))
                    self.assertEqual(len((await client.get("/runs?limit=1")).json()["runs"]), 1)

        with self.environment():
            asyncio.run(verify())

    def test_saved_approval_ticket_and_citation_details(self):
        for item in self.items:
            version = import_batch(self.engine, [item], tenant_id=None)[0]["id"]
            ingest_version(self.engine, version, MockEmbedding())

        async def verify():
            async def wait(client, run_id):
                for _ in range(300):
                    record = (await client.get(f"/runs/{run_id}")).json()
                    if record["status"] not in {"queued", "running"}:
                        return record
                    await asyncio.sleep(0.02)
                self.fail("run did not settle")

            async with (
                self.serving(app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url="http://test",
                    cookies={"supportops_session": app.state.sessions.issue("user-alpha")},
                ) as client,
            ):
                draft = (
                    await client.post(
                        "/runs", json={"question": "ws-alpha 429", "ticket_draft": True}
                    )
                ).json()
                paused = await wait(client, draft["id"])
                self.assertEqual(
                    (await client.get("/runs")).json()["runs"][0]["status"], "awaiting_approval"
                )
                action = paused["proposed_action"]
                response = await client.post(
                    f"/approvals/{action['approval_id']}/approve",
                    json={"run_id": draft["id"], "payload_digest": action["payload_digest"]},
                )
                self.assertEqual(response.status_code, 202)
                done = await wait(client, draft["id"])
                ticket = await client.get(f"/tickets/{done['ticket']['ticket_id']}")
                self.assertEqual(ticket.status_code, 200)
                knowledge = (
                    await client.post(
                        "/runs",
                        json={
                            "question": "starter配额是多少？",
                            "knowledge": True,
                            "product_version": "2.0",
                        },
                    )
                ).json()
                answered = await wait(client, knowledge["id"])
                self.assertTrue(answered["citations"])
                citation = answered["citations"][0]
                path = f"/runs/{knowledge['id']}/citations/{citation['chunk_id']}"
                self.assertEqual((await client.get(path)).json(), citation)
                foreign = {"Cookie": "supportops_session=" + app.state.sessions.issue("user-beta")}
                self.assertEqual((await client.get(path, headers=foreign)).status_code, 404)
                self.assertEqual((await client.get("/runs", headers=foreign)).json()["runs"], [])

        with self.environment():
            asyncio.run(verify())
