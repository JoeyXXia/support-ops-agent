"""Real PostgreSQL transport persistence and local write/knowledge regression."""

import asyncio
import os
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import sqlalchemy as sa
from support import PostgreSQLCase

from packages.agent.persistence import RunRepository, checkpoint_saver, execute_run
from packages.agent.scenario_mock import SupportScenarioMock
from packages.knowledge.embeddings import MockEmbedding
from packages.knowledge.ingestion import ingest_version
from packages.knowledge.repository import import_batch
from packages.storage.tickets import TicketRepository
from packages.tools.routing import TRANSPORT_ENV, build_executor
from packages.tools.schemas import AuthContext


class MCPRoutingTests(PostgreSQLCase):
    def setUp(self):
        super().setUp()
        self.context = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")
        self.checkpoint_engine = sa.create_engine(
            self.url.update_query_dict({"options": f"-csearch_path={self.schema},public"})
        )
        self.repository = RunRepository(self.checkpoint_engine)

    def tearDown(self):
        self.checkpoint_engine.dispose()
        super().tearDown()

    def pending(self, *, transport=None, draft=False, knowledge=False):
        record = {
            "id": str(uuid4()),
            "question": "starter配额是多少？" if knowledge else "ws-alpha 429",
            "status": "queued",
            "ticket_draft": draft,
        }
        if transport is not None:
            record["read_tool_transport"] = transport
        self.repository.create(record, self.context, knowledge=knowledge, product_version="2.0")
        return record["id"]

    def test_saved_mode_survives_config_change_and_legacy_stays_direct(self):
        async def verify():
            async with checkpoint_saver(self.checkpoint_engine) as saver:
                for saved_mode, current_mode in (
                    ("mcp", "direct"),
                    ("direct", "mcp"),
                    (None, "mcp"),
                ):
                    run_id = self.pending(transport=saved_mode)
                    with (
                        patch.dict(os.environ, {TRANSPORT_ENV: current_mode}),
                        patch(
                            "packages.agent.persistence.build_executor", wraps=build_executor
                        ) as factory,
                    ):
                        result = await execute_run(
                            self.repository, saver, run_id, self.context, SupportScenarioMock
                        )
                    self.assertEqual(factory.call_args.kwargs["transport"], saved_mode or "direct")
                    self.assertEqual((result["status"], result["tool_calls"]), ("completed", 2))
                    self.assertIn("60/60", result["answer"])
                    self.assertEqual(result.get("read_tool_transport"), saved_mode)
                    self.assertEqual(self.repository.load(run_id, self.context)["record"], result)

        asyncio.run(verify())

    def test_mcp_mode_retains_local_approval_write_and_knowledge(self):
        for item in self.items:
            version = import_batch(self.engine, [item], tenant_id=None)[0]["id"]
            ingest_version(self.engine, version, MockEmbedding())

        async def verify():
            async with checkpoint_saver(self.checkpoint_engine) as saver:
                draft_id = self.pending(transport="mcp", draft=True)
                paused = await execute_run(
                    self.repository, saver, draft_id, self.context, SupportScenarioMock
                )
                self.assertEqual(paused["status"], "awaiting_approval")
                action = paused["proposed_action"]
                TicketRepository(self.engine).decide(
                    action["approval_id"],
                    self.context,
                    run_id=draft_id,
                    digest=action["payload_digest"],
                    approve=True,
                )
                completed = await execute_run(
                    self.repository, saver, draft_id, self.context, SupportScenarioMock
                )
                self.assertEqual(
                    (completed["status"], completed["reason"]), ("completed", "ticket_created")
                )
                self.assertIsNotNone(completed["ticket"])
                knowledge_id = self.pending(transport="mcp", knowledge=True)
                answered = await execute_run(
                    self.repository, saver, knowledge_id, self.context, SupportScenarioMock
                )
                self.assertEqual(answered["status"], "completed")
                self.assertTrue(answered["citations"])

        # The local knowledge tool reads DATABASE_URL just as it does in the API process.
        database = self.checkpoint_engine.url.render_as_string(hide_password=False)
        with (
            patch.dict(os.environ, {"DATABASE_URL": database}),
            patch(
                "packages.tools.mcp_adapter.MCPReadAdapter.execute", new_callable=AsyncMock
            ) as remote,
        ):
            asyncio.run(verify())
            remote.assert_not_awaited()
