"""PostgreSQL repeat, contention, lost-response, rollback and upgrade evidence."""

import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import httpx2
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import DBAPIError, IntegrityError
from support import ROOT, PostgreSQLCase

from apps.api.main import app
from apps.api.persistent_runs import PersistentRunStore
from packages.agent.persistence import RunRepository
from packages.storage.schema import action_executions, approvals, tickets
from packages.storage.tickets import ApprovalConflict, TicketRepository
from packages.tools.schemas import AuthContext, TicketDraftArgs


class IdempotencyTests(PostgreSQLCase):
    def setUp(self):
        super().setUp()
        self.context = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")
        self.repository = TicketRepository(self.engine)
        self.args = TicketDraftArgs(
            workspace_id="ws-alpha", title="429排障", description="请求转人工"
        )

    def approved(self):
        run_id = str(uuid4())
        RunRepository(self.engine).create(
            {"id": run_id, "question": "ws-alpha 429", "status": "awaiting_approval"},
            self.context,
            knowledge=False,
            product_version=None,
        )
        proposal = self.repository.propose(
            run_id, self.args, self.context, target_tenant="tenant-alpha"
        )
        self.repository.decide(
            proposal.approval_id,
            self.context,
            run_id=run_id,
            digest=proposal.payload_digest,
            approve=True,
        )
        return proposal

    def counts(self):
        with self.engine.connect() as connection:
            return tuple(
                connection.scalar(sa.select(sa.func.count()).select_from(table))
                for table in (tickets, action_executions)
            )

    def create(self, proposal):
        return self.repository.create_ticket(
            proposal.approval_id,
            self.context,
            idempotency_key=proposal.idempotency_key,
            digest=proposal.payload_digest,
        )

    def test_ten_sequential_api_retries_and_mismatches(self):
        proposal = self.approved()

        async def verify():
            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://test"
                ) as client,
            ):
                app.state.runs = PersistentRunStore(self.engine, None)
                body = {
                    "approval_id": proposal.approval_id,
                    "idempotency_key": proposal.idempotency_key,
                    "payload_digest": proposal.payload_digest,
                }
                results = []
                for _ in range(11):
                    response = await client.post("/tickets", json=body)
                    self.assertEqual(response.status_code, 201)
                    results.append(response.json())
                self.assertTrue(all(result == results[0] for result in results))
                self.assertEqual(len(results), 11)
                with self.engine.begin() as connection:
                    connection.execute(
                        approvals.update()
                        .where(approvals.c.id == proposal.approval_id)
                        .values(expires_at=sa.func.now() - sa.text("interval '1 minute'"))
                    )
                replay = await client.post("/tickets", json=body)
                self.assertEqual(replay.status_code, 201)
                self.assertEqual(replay.json(), results[0])

                self.assertEqual(self.counts(), (1, 1))
                self.assertEqual(
                    (
                        await client.post("/tickets", json=dict(body, payload_digest="0" * 64))
                    ).status_code,
                    409,
                )
                other = self.approved()
                self.assertEqual(
                    (
                        await client.post(
                            "/tickets", json=dict(body, approval_id=other.approval_id)
                        )
                    ).status_code,
                    409,
                )
                with self.engine.begin() as connection:
                    connection.execute(
                        approvals.update()
                        .where(approvals.c.id == proposal.approval_id)
                        .values(params=dict(self.args.model_dump(), title="变更参数"))
                    )
                self.assertEqual((await client.post("/tickets", json=body)).status_code, 409)
                with self.assertRaises(ApprovalConflict):
                    self.repository.create_ticket(proposal.approval_id, self.context)
                self.assertEqual(self.counts(), (1, 1))

        asyncio.run(verify())

    def test_eight_concurrent_requests_and_unique_constraint(self):
        proposal = self.approved()
        barrier = Barrier(8)

        def request(_):
            barrier.wait(timeout=10)
            return self.create(proposal).model_dump(mode="json")

        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(request, range(8)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(self.counts(), (1, 1))
        with self.engine.begin() as connection:
            connection.execute(
                tickets.update()
                .where(tickets.c.id == results[0]["ticket_id"])
                .values(status="closed")
            )
        self.assertEqual(self.create(proposal).status, "open")
        self.assertEqual(
            self.repository.get_ticket(results[0]["ticket_id"], {"tenant-alpha"}).status, "closed"
        )
        other = self.approved()
        with self.assertRaises(IntegrityError) as error:
            with self.engine.begin() as connection:
                ticket_id = uuid4()
                connection.execute(
                    tickets.insert().values(
                        id=ticket_id,
                        tenant_id="tenant-alpha",
                        created_by=self.context.user_id,
                        status="open",
                        **self.args.model_dump(),
                    )
                )
                connection.execute(
                    action_executions.insert().values(
                        approval_id=other.approval_id,
                        tenant_id="tenant-alpha",
                        idempotency_key=proposal.idempotency_key,
                        payload_digest=other.payload_digest,
                        ticket_id=ticket_id,
                        result=results[0],
                    )
                )
        self.assertEqual(error.exception.orig.diag.constraint_name, "uq_execution_action_key")
        self.assertEqual(self.counts(), (1, 1))

    def test_lost_http_response_and_new_process_retry(self):
        proposal = self.approved()

        class LoseResponse(httpx2.AsyncBaseTransport):
            async def handle_async_request(self, request):
                response = await httpx2.ASGITransport(app=app).handle_async_request(request)
                await response.aread()
                await response.aclose()
                raise httpx2.ReadError("Day19 injected response loss after commit", request=request)

        async def verify():
            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(transport=LoseResponse(), base_url="http://test") as client,
            ):
                app.state.runs = PersistentRunStore(self.engine, None)
                with self.assertRaises(httpx2.ReadError):
                    await client.post(
                        "/tickets",
                        json={
                            "approval_id": proposal.approval_id,
                            "idempotency_key": proposal.idempotency_key,
                            "payload_digest": proposal.payload_digest,
                        },
                    )

        asyncio.run(verify())
        self.assertEqual(self.counts(), (1, 1))
        code = """
import sys
from packages.storage.database import engine_from_environment
from packages.storage.tickets import TicketRepository
from packages.tools.schemas import AuthContext
engine=engine_from_environment()
try:
    result=TicketRepository(engine).create_ticket(sys.argv[1],
        AuthContext(user_id="user-alpha",tenant_id="tenant-alpha",role="member"),
        idempotency_key=sys.argv[2],digest=sys.argv[3])
    print(result.model_dump_json())
finally:
    engine.dispose()
"""
        url = self.url.update_query_dict({"options": f"-csearch_path={self.schema},public"})
        env = dict(
            os.environ, DATABASE_URL=url.render_as_string(hide_password=False), PYTHONUTF8="1"
        )
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                proposal.approval_id,
                proposal.idempotency_key,
                proposal.payload_digest,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), self.create(proposal).model_dump(mode="json"))
        self.assertEqual(self.counts(), (1, 1))

    def test_rollback_at_status_and_result_write(self):
        for table, event, condition in (
            ("approvals", "UPDATE", "WHEN (NEW.status='executed')"),
            ("action_executions", "INSERT", ""),
        ):
            with self.subTest(table=table):
                proposal = self.approved()
                before = self.counts()
                with self.engine.begin() as connection:
                    connection.execute(
                        sa.text("""CREATE FUNCTION day19_fail() RETURNS trigger
                        LANGUAGE plpgsql AS $$ BEGIN
                        RAISE EXCEPTION 'Day19 injected failure';
                        END $$""")
                    )
                    connection.execute(
                        sa.text(f"""CREATE TRIGGER day19_write_failure BEFORE {event}
                        ON {table} FOR EACH ROW {condition} EXECUTE FUNCTION day19_fail()""")
                    )
                with self.assertRaises(DBAPIError):
                    self.create(proposal)
                self.assertEqual(self.counts(), before)
                with self.engine.begin() as connection:
                    row = (
                        connection.execute(
                            sa.select(approvals).where(approvals.c.id == proposal.approval_id)
                        )
                        .mappings()
                        .one()
                    )
                    self.assertEqual((row["status"], row["ticket_id"]), ("approved", None))
                    connection.execute(sa.text(f"DROP TRIGGER day19_write_failure ON {table}"))
                    connection.execute(sa.text("DROP FUNCTION day19_fail()"))
                self.create(proposal)
                self.assertEqual(self.counts(), (before[0] + 1, before[1] + 1))

    def test_backfill_preexisting_executed_approval(self):
        proposal = self.approved()
        expected = self.create(proposal)
        with self.engine.begin() as connection:
            connection.execute(
                approvals.update()
                .where(approvals.c.id == proposal.approval_id)
                .values(payload_digest=None)
            )
        config = Config(str(ROOT / "alembic.ini"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0005_approval_binding")
            command.upgrade(config, "head")
        self.assertEqual(self.create(proposal), expected)
        self.assertEqual(self.counts(), (1, 1))
