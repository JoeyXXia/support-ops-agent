import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx2

from apps.api.main import app


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(
            os.environ, {"SUPPORTOPS_PERSIST_RUNS": "0", "SUPPORTOPS_DEMO_PASSWORDS": ""}
        )
        self.environment.start()
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        self.client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://test",
            cookies={"supportops_session": app.state.sessions.issue("user-alpha")},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.environment.stop()

    async def create(self):
        response = await self.client.post("/runs", json={"question": "ws-alpha 429"})
        self.assertEqual(response.status_code, 202)
        return response.json()["id"]

    async def test_history_is_bounded_ordered_and_owned(self):
        first, second = await self.create(), await self.create()
        response = (await self.client.get("/runs?limit=1")).json()
        self.assertEqual(response["storage"], "memory")
        self.assertEqual([r["id"] for r in response["runs"]], [second])
        self.assertIsNotNone(response["runs"][0]["created_at"])
        self.assertNotIn("answer", response["runs"][0])
        self.assertNotIn("events", response["runs"][0])
        rows = (await self.client.get("/runs")).json()["runs"]
        self.assertEqual([r["id"] for r in rows], [second, first])
        for user in ("user-beta", "support-alpha"):
            response = await self.client.get(
                "/runs", headers={"Cookie": "supportops_session=" + app.state.sessions.issue(user)}
            )
            self.assertEqual(response.json()["runs"], [])

    async def test_anonymous_and_invalid_limits_rejected(self):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test"
        ) as client:
            self.assertEqual((await client.get("/runs")).status_code, 401)
        for value in ("0", "51", "-1", "invalid"):
            self.assertEqual((await self.client.get("/runs?limit=" + value)).status_code, 422)

    async def test_citation_is_scoped_to_run_and_never_opens_source_path(self):
        run_id = await self.create()
        await asyncio.gather(*list(app.state.runs.tasks))
        chunk_id = str(uuid4())
        citation = {
            "chunk_id": chunk_id,
            "source": "synthetic#example",
            "source_path": "../../.env.local",
            "body": "synthetic saved excerpt",
            "product_version": "2.0",
            "version_number": 1,
            "start_line": 1,
            "end_line": 2,
        }
        app.state.runs.records[run_id]["citations"] = [citation]
        path = f"/runs/{run_id}/citations/{chunk_id}"
        # Initialize the other principal's fixed seed fixtures before guarding citation reads.
        # This keeps the assertion about resource lookup, not unrelated store startup.
        await self.client.get(
            "/session",
            headers={"Cookie": "supportops_session=" + app.state.sessions.issue("user-beta")},
        )

        with patch.object(
            Path, "read_text", side_effect=AssertionError("Must not open any path")
        ) as read:
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), citation)
            self.assertEqual(
                (await self.client.get(f"/runs/{run_id}/citations/{uuid4()}")).status_code, 404
            )
            self.assertEqual(
                (await self.client.get(f"/runs/{uuid4()}/citations/{chunk_id}")).status_code, 404
            )
            self.assertEqual(
                (
                    await self.client.get(
                        path,
                        headers={
                            "Cookie": "supportops_session=" + app.state.sessions.issue("user-beta")
                        },
                    )
                ).status_code,
                404,
            )
            response = await self.client.get(f"/runs/{run_id}/citations/..%2f..%2f.env.local")
            self.assertIn(response.status_code, {404, 422})
            read.assert_not_called()

    async def test_legacy_time_is_not_invented(self):
        run_id = await self.create()
        await asyncio.gather(*list(app.state.runs.tasks))
        del app.state.runs.records[run_id]["created_at"]
        row = (await self.client.get("/runs")).json()["runs"][0]
        self.assertIsNone(row["created_at"])
