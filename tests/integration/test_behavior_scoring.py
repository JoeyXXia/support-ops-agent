"""Grade actual persisted approval results using a mock model and PostgreSQL."""

import asyncio
from unittest.mock import patch
from uuid import uuid4

import sqlalchemy as sa
from support import PostgreSQLCase

from evals.behavior import ExpectedRun, score_behavior
from packages.agent.persistence import RunRepository, checkpoint_saver, execute_run
from packages.agent.scenario_mock import SupportScenarioMock
from packages.storage.schema import action_executions, tickets
from packages.storage.tickets import TicketRepository
from packages.tools.executor import ToolExecutor
from packages.tools.schemas import AuthContext


class BehaviorDatabaseTests(PostgreSQLCase):
    def test_real_commit_passes_and_claim_without_commit_fails(self):
        engine = sa.create_engine(
            self.url.update_query_dict({"options": f"-csearch_path={self.schema},public"})
        )
        self.addCleanup(engine.dispose)
        repository = RunRepository(engine)
        context = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")
        question = "ws-alpha 429"
        params = {
            "workspace_id": "ws-alpha",
            "title": "CloudDesk 429 排障转人工",
            "description": question,
        }

        async def verify():
            async with checkpoint_saver(engine) as saver:
                for approving in (False, True):
                    attempts = []
                    real_execute = ToolExecutor.execute_async

                    async def capture(executor, name, arguments, *, context):
                        result = await real_execute(executor, name, arguments, context=context)
                        attempts.append({"name": name, "arguments": arguments, "ok": result.ok})
                        return result

                    run_id = str(uuid4())
                    repository.create(
                        {
                            "id": run_id,
                            "status": "queued",
                            "question": question,
                            "ticket_draft": True,
                        },
                        context,
                        knowledge=False,
                        product_version=None,
                    )
                    with patch.object(ToolExecutor, "execute_async", capture):
                        paused = await execute_run(
                            repository, saver, run_id, context, SupportScenarioMock
                        )
                        action = paused["proposed_action"]
                        TicketRepository(engine).decide(
                            action["approval_id"],
                            context,
                            run_id=run_id,
                            digest=action["payload_digest"],
                            approve=approving,
                        )
                        run = await execute_run(
                            repository, saver, run_id, context, SupportScenarioMock
                        )
                    trace_ids = [
                        event["call_id"]
                        for event in run["events"]
                        if event["kind"] == "tool.started"
                    ]
                    with engine.connect() as connection:
                        stored = [
                            dict(
                                ticket_id=str(row.id),
                                tenant_id=row.tenant_id,
                                workspace_id=row.workspace_id,
                                title=row.title,
                                description=row.description,
                                status=row.status,
                            )
                            for row in connection.execute(sa.select(tickets))
                        ]
                        receipts = [
                            str(value)
                            for value in connection.scalars(
                                sa.select(action_executions.c.ticket_id)
                            )
                        ]
                    observed = {
                        key: run[key]
                        for key in (
                            "status",
                            "reason",
                            "steps",
                            "write_calls",
                            "tool_calls",
                            "ticket",
                        )
                    }
                    observed.update(
                        attempts=[
                            dict(attempt, call_id=call_id)
                            for attempt, call_id in zip(attempts, trace_ids, strict=True)
                        ],
                        committed_tickets=stored,
                        receipt_ticket_ids=receipts,
                    )
                    winning = ExpectedRun(
                        case_id="accept-ticket-01",
                        tools=[{"name": "propose_ticket", "arguments": params}],
                        max_steps=6,
                        max_tool_calls=8,
                        max_write_calls=3,
                        status="completed",
                        reason="ticket_created",
                        written_tickets=1,
                        ticket={"status": "open", "tenant_id": "tenant-alpha", **params},
                    )
                    if approving:
                        self.assertTrue(score_behavior(winning, observed)["passed"])
                    else:
                        self.assertEqual((stored, receipts), ([], []))
                        negative = winning.model_copy(
                            update={
                                "case_id": "reject-ticket-01",
                                "written_tickets": 0,
                                "ticket": None,
                                "max_write_calls": 0,
                                "reason": "approval_rejected",
                            }
                        )
                        self.assertTrue(score_behavior(negative, observed)["passed"])
                        fabricated = dict(
                            observed, status="completed", reason="ticket_created", write_calls=1
                        )
                        self.assertIn(
                            "unexpected_committed_ticket_count",
                            score_behavior(winning, fabricated)["reasons"],
                        )

        asyncio.run(verify())
