import asyncio
import json
import os
from unittest.mock import patch

import httpx2
import sqlalchemy as sa
from support import PostgreSQLCase

from apps.api.main import app
from packages.knowledge.embeddings import MockEmbedding
from packages.knowledge.ingestion import ingest_version
from packages.knowledge.repository import import_batch
from packages.storage.schema import chunks, document_versions, documents
from packages.tools.executor import ToolExecutor

PASSWORDS = {
    u: "synthetic-only-" + u + "-credential"
    for u in ("user-alpha", "user-beta", "support-alpha", "support-beta")
}


class SessionIsolationTests(PostgreSQLCase):
    def test_real_login_resource_ids_and_private_document_scopes(self):
        url = self.url.update_query_dict({"options": f"-csearch_path={self.schema},public"})
        for item, tenant in (
            (self.items[0], None),
            (self.items[1], "tenant-alpha"),
            (self.items[2], "tenant-beta"),
        ):
            version_id = import_batch(self.engine, [item], tenant_id=tenant)[0]["id"]
            ingest_version(self.engine, version_id, MockEmbedding(), tenant_id=tenant)

        async def verify():
            async def login(client, user):
                r = await client.post(
                    "/session", json={"username": user, "password": PASSWORDS[user]}
                )
                self.assertEqual(r.status_code, 200)

            async def wait(client, run_id):
                for _ in range(300):
                    r = await client.get(f"/runs/{run_id}")
                    self.assertEqual(r.status_code, 200)
                    record = r.json()
                    if record["status"] not in {"queued", "running"}:
                        return record
                    await asyncio.sleep(0.02)
                self.fail("run did not settle")

            async with (
                self.serving(app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://test"
                ) as client,
            ):
                await login(client, "user-alpha")
                created = await client.post(
                    "/runs", json={"question": "ws-alpha 429", "ticket_draft": True}
                )
                run_id = created.json()["id"]
                proposal = (await wait(client, run_id))["proposed_action"]
                self.assertEqual(
                    (await client.get(f"/approvals/{proposal['approval_id']}")).status_code, 200
                )
                for user in ("user-beta", "support-alpha", "support-beta"):
                    await login(client, user)
                    self.assertEqual((await client.get(f"/runs/{run_id}")).status_code, 404)
                    self.assertEqual((await client.post(f"/runs/{run_id}/resume")).status_code, 404)
                    self.assertEqual(
                        (await client.get(f"/approvals/{proposal['approval_id']}")).status_code, 404
                    )
                    for decision in ("approve", "reject"):
                        r = await client.post(
                            f"/approvals/{proposal['approval_id']}/{decision}",
                            json={"run_id": run_id, "payload_digest": proposal["payload_digest"]},
                        )
                        self.assertEqual(r.status_code, 404)
                    r = await client.post("/tickets", json={"approval_id": proposal["approval_id"]})
                    self.assertEqual(r.status_code, 404)
                await login(client, "user-alpha")
                r = await client.post(
                    f"/approvals/{proposal['approval_id']}/approve",
                    json={"run_id": run_id, "payload_digest": proposal["payload_digest"]},
                )
                self.assertEqual(r.status_code, 202)
                done = await wait(client, run_id)
                self.assertEqual(done["reason"], "ticket_created")
                ticket_id = done["ticket"]["ticket_id"]
                self.assertEqual((await client.get(f"/tickets/{ticket_id}")).status_code, 200)
                for user in ("user-beta", "support-beta"):
                    await login(client, user)
                    self.assertEqual((await client.get(f"/tickets/{ticket_id}")).status_code, 404)
                # Same-tenant support may read tickets, but cannot approve another actor's run.
                await login(client, "support-alpha")
                self.assertEqual((await client.get(f"/tickets/{ticket_id}")).status_code, 200)

                # HTTP knowledge run exposes citations only through its owner-protected run URL.
                await login(client, "user-alpha")
                r = await client.post(
                    "/runs",
                    json={
                        "question": "reason=monthly_quota_exhausted，持续重试有用吗？",
                        "knowledge": True,
                        "product_version": "2.0",
                    },
                )
                knowledge_id = r.json()["id"]
                knowledge_record = await wait(client, knowledge_id)
                self.assertTrue(knowledge_record["citations"])
                await login(client, "user-beta")
                self.assertEqual((await client.get(f"/runs/{knowledge_id}")).status_code, 404)

                # Verify actual private chunk membership, not only a textual answer.
                with self.engine.connect() as conn:
                    private = dict(
                        conn.execute(
                            sa.select(chunks.c.id, documents.c.tenant_id)
                            .select_from(
                                chunks.join(
                                    document_versions, chunks.c.version_id == document_versions.c.id
                                ).join(documents, document_versions.c.document_id == documents.c.id)
                            )
                            .where(documents.c.tenant_id.is_not(None))
                        ).all()
                    )
                self.assertTrue(private)
                executor = ToolExecutor(engine=self.engine)
                for user in PASSWORDS:
                    context = app.state.sessions.subjects[user]
                    item = self.items[1] if context.tenant_id == "tenant-alpha" else self.items[2]
                    result = executor.execute(
                        "search_docs",
                        {"question": item.body, "product_version": "2.0"},
                        context=context,
                    )
                    self.assertTrue(result.ok, result)
                    from uuid import UUID

                    self.assertTrue(
                        any(
                            private.get(UUID(row.chunk_id)) == context.tenant_id
                            for row in result.data.sources
                        )
                    )
                    for source in result.data.sources:
                        tenant = private.get(UUID(source.chunk_id))
                        self.assertTrue(tenant is None or tenant == context.tenant_id)

        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": url.render_as_string(hide_password=False),
                "SUPPORTOPS_PERSIST_RUNS": "1",
                "SUPPORTOPS_DEMO_PASSWORDS": json.dumps(PASSWORDS),
            },
        ):
            asyncio.run(verify())
