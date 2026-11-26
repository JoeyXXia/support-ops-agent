import asyncio
import json
import os
from unittest.mock import patch

import httpx2
from support import PostgreSQLCase

from apps.api.main import app


class DurableEventTests(PostgreSQLCase):
    def test_approval_phase_then_execution_phase_and_foreign_subscription(self):
        def events(response):
            self.assertEqual(response.status_code, 200)
            return [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]

        async def verify():
            async with (
                self.serving(app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url="http://test",
                    cookies={"supportops_session": app.state.sessions.issue("user-alpha")},
                ) as client,
            ):
                run = (
                    await client.post(
                        "/runs", json={"question": "ws-alpha 429", "ticket_draft": True}
                    )
                ).json()
                path = f"/runs/{run['id']}/events"
                first = events(await client.get(path))
                self.assertEqual(first[-1]["type"], "approval.required")
                paused = first[-1]["data"]["run"]
                self.assertEqual(paused["status"], "awaiting_approval")
                self.assertNotIn("run.finished", [e["type"] for e in first])
                self.assertEqual(
                    (
                        await client.get(
                            path,
                            headers={
                                "Cookie": "supportops_session="
                                + app.state.sessions.issue("user-beta")
                            },
                        )
                    ).status_code,
                    404,
                )
                proposal = paused["proposed_action"]
                approved = await client.post(
                    f"/approvals/{proposal['approval_id']}/approve",
                    json={"run_id": run["id"], "payload_digest": proposal["payload_digest"]},
                )
                self.assertEqual(approved.status_code, 202)
                second = events(await client.get(path))
                self.assertEqual(second[-1]["type"], "run.finished")
                result = second[-1]["data"]["run"]
                self.assertEqual(
                    (result["status"], result["reason"]), ("completed", "ticket_created")
                )
                self.assertTrue(result["ticket"]["ticket_id"])
                self.assertEqual(app.state.runs.active_streams, 0)

        url = self.url.update_query_dict({"options": f"-csearch_path={self.schema},public"})
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": url.render_as_string(hide_password=False),
                "SUPPORTOPS_PERSIST_RUNS": "1",
                "SUPPORTOPS_DEMO_PASSWORDS": "",
            },
        ):
            asyncio.run(verify())
