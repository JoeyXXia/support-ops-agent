"""Real PostgreSQL proposal, ownership and guarded execution checks."""

import asyncio
import os
from unittest.mock import patch
from uuid import uuid4

import httpx2
import sqlalchemy as sa
from support import PostgreSQLCase

from apps.api.main import app
from packages.agent.graph import GraphAgent
from packages.agent.persistence import RunRepository
from packages.agent.ticket_mock import TicketDraftMock
from packages.storage.schema import approvals, tickets
from packages.storage.tickets import TicketRepository
from packages.tools.executor import ToolExecutor
from packages.tools.schemas import AuthContext, TicketDraftArgs


class TicketTests(PostgreSQLCase):
    def setUp(self):
        super().setUp()
        self.alpha = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")
        self.beta = AuthContext(user_id="user-beta", tenant_id="tenant-beta", role="member")
        self.run_id = str(uuid4())
        RunRepository(self.engine).create(
            {
                "id": self.run_id,
                "question": "CloudDesk ws-alpha 429，生成工单草稿。",
                "status": "queued",
            },
            self.alpha,
            knowledge=False,
            product_version=None,
        )
        self.executor = ToolExecutor(engine=self.engine, run_id=self.run_id)
        self.repository = TicketRepository(self.engine)
        self.args = TicketDraftArgs(
            workspace_id="ws-alpha", title="429排障", description="重试仍然429"
        )

    def counts(self):
        with self.engine.connect() as connection:
            return tuple(
                connection.scalar(sa.select(sa.func.count()).select_from(table))
                for table in (tickets, approvals)
            )

    def test_agent_draft_only_and_replay_preserves_payload(self):
        result = asyncio.run(
            GraphAgent(TicketDraftMock("仍然429"), self.executor).run("仍然429", context=self.alpha)
        )
        self.assertEqual(result.status, "completed")
        proposal = result.proposed_action
        self.assertEqual((proposal["tenant_id"], proposal["status"]), ("tenant-alpha", "pending"))
        self.assertEqual(proposal["params"]["description"], "仍然429")
        self.assertEqual(self.counts(), (0, 1))
        replay = self.executor.execute("propose_ticket", proposal["params"], context=self.alpha)
        self.assertTrue(replay.ok)
        self.assertEqual(replay.data.approval_id, proposal["approval_id"])
        self.assertEqual(self.counts(), (0, 1))
        changed = dict(proposal["params"], title="换个标题")
        self.assertFalse(self.executor.execute("propose_ticket", changed, context=self.alpha).ok)
        self.assertEqual(self.counts(), (0, 1))
        with self.assertRaises(PermissionError):
            self.repository.create_ticket(proposal["approval_id"], self.alpha)
        self.assertEqual(self.counts(), (0, 1))

    def test_scoped_reads_and_server_execution_guard(self):
        proposal = self.repository.propose(
            self.run_id, self.args, self.alpha, target_tenant="tenant-alpha"
        )
        with self.assertRaises(LookupError):
            self.repository.create_ticket(proposal.approval_id, self.beta)
        # Only a trusted test fixture changes status. Day 17 exposes no approve endpoint.
        with self.engine.begin() as connection:
            connection.execute(
                approvals.update()
                .where(approvals.c.id == proposal.approval_id)
                .values(status="approved", decided_by=self.alpha.user_id, decided_at=sa.func.now())
            )
        created = self.repository.create_ticket(proposal.approval_id, self.alpha)
        self.assertEqual(created.title, self.args.title)
        self.assertEqual(self.counts(), (1, 1))
        self.assertEqual(self.repository.create_ticket(proposal.approval_id, self.alpha), created)
        own = self.executor.execute(
            "get_ticket", {"ticket_id": created.ticket_id}, context=self.alpha
        )
        self.assertTrue(own.ok)
        foreign = self.executor.execute(
            "get_ticket", {"ticket_id": created.ticket_id}, context=self.beta
        )
        absent = self.executor.execute("get_ticket", {"ticket_id": str(uuid4())}, context=self.beta)
        self.assertEqual(foreign, absent)
        self.assertEqual(foreign.error.code, "not_found")

    def test_forged_identity_and_direct_model_creation_rejected(self):
        for extra in (
            {"tenant_id": "tenant-beta"},
            {"status": "approved"},
            {"run_id": str(uuid4())},
        ):
            result = self.executor.execute(
                "propose_ticket", dict(self.args.model_dump(), **extra), context=self.alpha
            )
            self.assertEqual(result.error.code, "invalid_arguments")
        self.assertEqual(
            self.executor.execute("create_ticket", {}, context=self.alpha).error.code,
            "unknown_tool",
        )
        cross = self.executor.execute(
            "propose_ticket",
            dict(self.args.model_dump(), workspace_id="ws-beta"),
            context=self.alpha,
        )
        self.assertEqual(cross.error.code, "not_found")
        self.assertEqual(self.counts(), (0, 0))

    def test_api_draft_visible_and_pending_cannot_execute(self):
        async def verify():
            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://test"
                ) as client,
            ):
                response = await client.post(
                    "/runs", json={"question": "ws-alpha 429，请转人工。", "ticket_draft": True}
                )
                self.assertEqual(response.status_code, 202)
                run_id = response.json()["id"]
                for _ in range(300):
                    record = (await client.get(f"/runs/{run_id}")).json()
                    if record["status"] not in {"queued", "running"}:
                        break
                    await asyncio.sleep(0.02)
                self.assertEqual(record["status"], "awaiting_approval")
                proposal = record["proposed_action"]
                self.assertEqual(proposal["params"]["description"], "ws-alpha 429，请转人工。")
                self.assertEqual(self.counts(), (0, 1))
                denied = await client.post(
                    "/tickets", json={"approval_id": proposal["approval_id"]}
                )
                self.assertEqual(denied.status_code, 409)
                forged = await client.post(
                    "/tickets", json={"approval_id": proposal["approval_id"], "status": "approved"}
                )
                self.assertEqual(forged.status_code, 422)
                self.assertEqual(self.counts(), (0, 1))
                self.assertEqual((await client.post(f"/runs/{run_id}/resume")).json(), record)

        url = self.url.update_query_dict({"options": f"-csearch_path={self.schema},public"})
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": url.render_as_string(hide_password=False),
                "SUPPORTOPS_PERSIST_RUNS": "1",
            },
        ):
            asyncio.run(verify())
