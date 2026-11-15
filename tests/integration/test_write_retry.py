import asyncio
import os
from unittest.mock import patch

import httpx2
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from support import PostgreSQLCase

from apps.api.main import app
from packages.storage.schema import action_executions, tickets
from packages.storage.tickets import TicketRepository


class WriteRetryTests(PostgreSQLCase):
    def test_commit_then_connection_loss_retries_same_action_once(self):
        original = TicketRepository.create_ticket
        attempts = []

        class LostConnection(Exception):
            sqlstate = "08006"

        def write(repository, approval_id, context, **kwargs):
            result = original(repository, approval_id, context, **kwargs)
            attempts.append((str(approval_id), kwargs["idempotency_key"], kwargs["digest"]))
            if len(attempts) == 1:
                raise OperationalError(None, None, LostConnection(), connection_invalidated=True)
            return result

        record = self.run_flow(write)
        self.assertEqual((record["reason"], record["write_calls"]), ("ticket_created", 2))
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(self.counts(), (1, 1))
        self.assertTrue(
            any(
                e["kind"] == "retry.scheduled" and e["operation"] == "write"
                for e in record["events"]
            )
        )

    def test_write_permission_error_is_not_retried(self):
        attempts = []

        def denied(*args, **kwargs):
            attempts.append(1)
            raise PermissionError("synthetic denial")

        record = self.run_flow(denied)
        self.assertEqual((record["reason"], record["write_calls"]), ("approval_invalid", 1))
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.counts(), (0, 0))

    def counts(self):
        with self.engine.connect() as connection:
            return tuple(
                connection.scalar(sa.select(sa.func.count()).select_from(table))
                for table in (tickets, action_executions)
            )

    def run_flow(self, write):
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
                with patch.object(
                    TicketRepository, "create_ticket", side_effect=write, autospec=True
                ):
                    response = await client.post(
                        f"/approvals/{proposal['approval_id']}/approve",
                        json={"run_id": record["id"], "payload_digest": proposal["payload_digest"]},
                    )
                    self.assertEqual(response.status_code, 202)
                    return await wait(client, record["id"])

        url = self.url.update_query_dict({"options": f"-csearch_path={self.schema},public"})
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": url.render_as_string(hide_password=False),
                "SUPPORTOPS_PERSIST_RUNS": "1",
            },
        ):
            return asyncio.run(verify())
