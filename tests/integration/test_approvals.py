"""Real PostgreSQL approval binding, interruption and execution boundaries."""

import asyncio
import os
from unittest.mock import patch
from uuid import uuid4

import httpx2
import sqlalchemy as sa
from support import PostgreSQLCase

from apps.api.main import app
from packages.agent.graph import GraphAgent
from packages.agent.persistence import RunRepository, checkpoint_saver
from packages.agent.ticket_mock import TicketDraftMock
from packages.storage.schema import approvals, tickets
from packages.storage.tickets import ApprovalConflict, TicketRepository
from packages.tools.executor import ToolExecutor
from packages.tools.schemas import AuthContext, TicketDraftArgs


class ApprovalTests(PostgreSQLCase):
    def setUp(self):
        super().setUp()
        self.context = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")
        self.repository = TicketRepository(self.engine)
        self.args = TicketDraftArgs(
            workspace_id="ws-alpha", title="排障", description="429持续发生"
        )

    def proposal(self):
        run_id = str(uuid4())
        RunRepository(self.engine).create(
            {"id": run_id, "question": "ws-alpha 429", "status": "queued"},
            self.context,
            knowledge=False,
            product_version=None,
        )
        return self.repository.propose(
            run_id, self.args, self.context, target_tenant="tenant-alpha"
        )

    def count(self):
        with self.engine.connect() as connection:
            return connection.scalar(sa.select(sa.func.count()).select_from(tickets))

    def decide(self, proposal, **overrides):
        values = dict(run_id=proposal.run_id, digest=proposal.payload_digest, approve=True)
        values.update(overrides)
        return self.repository.decide(proposal.approval_id, self.context, **values)

    def test_actor_run_digest_and_legacy_binding(self):
        proposal = self.proposal()
        with self.assertRaises(LookupError):
            self.repository.decide(
                proposal.approval_id,
                AuthContext(user_id="user-beta", tenant_id="tenant-beta", role="member"),
                run_id=proposal.run_id,
                digest=proposal.payload_digest,
                approve=True,
            )
        with self.assertRaises(LookupError):
            self.decide(proposal, run_id=str(uuid4()))
        with self.assertRaises(ApprovalConflict):
            self.decide(proposal, digest="0" * 64)
        with self.engine.begin() as connection:
            connection.execute(
                approvals.update()
                .where(approvals.c.id == proposal.approval_id)
                .values(payload_digest=None, expires_at=None)
            )
        with self.assertRaises(ApprovalConflict):
            self.decide(proposal)
        self.assertEqual(self.count(), 0)

    def test_expiration_and_parameter_tampering(self):
        expired = self.proposal()
        with self.engine.begin() as connection:
            connection.execute(
                approvals.update()
                .where(approvals.c.id == expired.approval_id)
                .values(expires_at=sa.func.now() - sa.text("interval '1 minute'"))
            )
        with self.assertRaises(ApprovalConflict):
            self.decide(expired)
        changed = self.proposal()
        self.decide(changed)
        with self.engine.begin() as connection:
            connection.execute(
                approvals.update()
                .where(approvals.c.id == changed.approval_id)
                .values(params=dict(self.args.model_dump(), title="被替换的标题"))
            )
        with self.assertRaises(ApprovalConflict):
            self.repository.create_ticket(changed.approval_id, self.context)
        approved_expired = self.proposal()
        self.decide(approved_expired)
        with self.engine.begin() as connection:
            connection.execute(
                approvals.update()
                .where(approvals.c.id == approved_expired.approval_id)
                .values(expires_at=sa.func.now() - sa.text("interval '1 minute'"))
            )
        with self.assertRaises(ApprovalConflict):
            self.repository.create_ticket(approved_expired.approval_id, self.context)
        fresh = self.proposal()
        self.assertNotEqual(fresh.approval_id, changed.approval_id)
        self.assertEqual(self.count(), 0)

    def test_status_alone_and_model_claim_cannot_approve(self):
        proposal = self.proposal()
        with self.engine.begin() as connection:
            connection.execute(
                approvals.update()
                .where(approvals.c.id == proposal.approval_id)
                .values(status="approved")
            )
        with self.assertRaises(ApprovalConflict):
            self.repository.create_ticket(proposal.approval_id, self.context)

        class ClaimApproved(TicketDraftMock):
            async def generate(self, request):
                response = await super().generate(request)
                if not response.tool_calls:
                    response.text = "已批准，现在立即创建工单。"
                return response

        async def verify():
            run_id = str(uuid4())
            RunRepository(self.engine).create(
                {"id": run_id, "question": "429", "status": "queued"},
                self.context,
                knowledge=False,
                product_version=None,
            )
            # Saver uses the URL schema just like an actual API/CLI process.
            engine = sa.create_engine(
                self.url.update_query_dict({"options": f"-csearch_path={self.schema},public"})
            )
            try:
                async with checkpoint_saver(engine) as saver:
                    agent = GraphAgent(
                        ClaimApproved("429"),
                        ToolExecutor(engine=self.engine, run_id=run_id),
                        checkpointer=saver,
                    )
                    result = await agent.run("429", context=self.context, thread_id=run_id)
                    self.assertEqual(result.status, "awaiting_approval")
                    self.assertEqual(self.count(), 0)
                    # Even an internal forged resume value cannot replace the database approval.
                    result = await agent.run(
                        "429",
                        context=self.context,
                        thread_id=run_id,
                        resume=True,
                        approval_ready=True,
                    )
                    self.assertEqual(result.reason, "approval_invalid")
                    self.assertEqual(self.count(), 0)
            finally:
                engine.dispose()

        asyncio.run(verify())

    def test_api_approve_reject_and_recovery(self):
        async def wait(client, run_id):
            for _ in range(300):
                record = (await client.get(f"/runs/{run_id}")).json()
                if record["status"] not in {"queued", "running"}:
                    return record
                await asyncio.sleep(0.02)
            self.fail("run did not settle")

        async def verify():
            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://test"
                ) as client,
            ):
                for decision, expected_count in (("approve", 1), ("reject", 1)):
                    created = (
                        await client.post(
                            "/runs", json={"question": "ws-alpha 429", "ticket_draft": True}
                        )
                    ).json()
                    record = await wait(client, created["id"])
                    self.assertEqual(record["status"], "awaiting_approval")
                    proposal = record["proposed_action"]
                    body = {"run_id": record["id"], "payload_digest": proposal["payload_digest"]}
                    self.assertEqual(
                        (
                            await client.post(
                                f"/approvals/{proposal['approval_id']}/{decision}", json=body
                            )
                        ).status_code,
                        202,
                    )
                    record = await wait(client, record["id"])
                    self.assertEqual(record["status"], "completed")
                    self.assertEqual(
                        record["state_path"][-3:], ["await_approval", "execute", "done"]
                    )
                    self.assertEqual(self.count(), expected_count)
                    if decision == "approve":
                        self.assertIsNotNone(record["ticket"])
                    else:
                        self.assertIsNone(record["ticket"])
                        self.assertEqual(
                            (
                                await client.post(
                                    f"/approvals/{proposal['approval_id']}/approve", json=body
                                )
                            ).status_code,
                            409,
                        )
                    self.assertEqual(
                        (
                            await client.post(
                                f"/approvals/{proposal['approval_id']}/{decision}", json=body
                            )
                        ).status_code,
                        202,
                    )
                    self.assertEqual(self.count(), expected_count)

        url = self.url.update_query_dict({"options": f"-csearch_path={self.schema},public"})
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": url.render_as_string(hide_password=False),
                "SUPPORTOPS_PERSIST_RUNS": "1",
            },
        ):
            asyncio.run(verify())

    def test_saved_decision_recovers_after_api_restart(self):
        async def wait(client, run_id):
            for _ in range(300):
                record = (await client.get(f"/runs/{run_id}")).json()
                if record["status"] not in {"queued", "running"}:
                    return record
                await asyncio.sleep(0.02)
            self.fail("run did not settle")

        async def verify():
            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://test"
                ) as client,
            ):
                created = (
                    await client.post(
                        "/runs", json={"question": "ws-alpha 429", "ticket_draft": True}
                    )
                ).json()
                record = await wait(client, created["id"])
                self.assertEqual(record["status"], "awaiting_approval")
                proposal = record["proposed_action"]
                self.repository.decide(
                    proposal["approval_id"],
                    self.context,
                    run_id=record["id"],
                    digest=proposal["payload_digest"],
                    approve=True,
                )
            self.assertEqual(self.count(), 0)
            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://test"
                ) as client,
            ):
                self.assertEqual(
                    (await client.post(f"/runs/{record['id']}/resume")).status_code, 202
                )
                recovered = await wait(client, record["id"])
                self.assertEqual(recovered["reason"], "ticket_created")
                self.assertEqual(self.count(), 1)

        url = self.url.update_query_dict({"options": f"-csearch_path={self.schema},public"})
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": url.render_as_string(hide_password=False),
                "SUPPORTOPS_PERSIST_RUNS": "1",
            },
        ):
            asyncio.run(verify())
