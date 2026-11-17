import json
import os
import unittest
from unittest.mock import patch

import httpx2

from apps.api.main import app
from apps.api.sessions import COOKIE

PASSWORDS = {
    user: "synthetic-only-" + user + "-credential"
    for user in ("user-alpha", "support-alpha", "user-beta", "support-beta")
}


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(
            os.environ,
            {"SUPPORTOPS_DEMO_PASSWORDS": json.dumps(PASSWORDS), "SUPPORTOPS_PERSIST_RUNS": "0"},
        )
        self.environment.start()
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        self.client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.environment.stop()

    async def login(self, user="user-alpha"):
        result = await self.client.post(
            "/session", json={"username": user, "password": PASSWORDS[user]}
        )
        self.assertEqual(result.status_code, 200)
        return result

    async def test_anonymous_forged_identity_and_cross_origin(self):
        self.assertEqual((await self.client.get("/health")).status_code, 200)
        for path in (
            "/session",
            "/runs/00000000-0000-0000-0000-000000000001",
            "/tickets/00000000-0000-0000-0000-000000000001",
        ):
            self.assertEqual(
                (
                    await self.client.get(
                        path, headers={"X-User-ID": "user-alpha", "X-Tenant-ID": "tenant-alpha"}
                    )
                ).status_code,
                401,
            )
        result = await self.client.post(
            "/session",
            headers={"Origin": "https://evil.invalid"},
            json={"username": "user-alpha", "password": PASSWORDS["user-alpha"]},
        )
        self.assertEqual(result.status_code, 403)

    async def test_login_logout_rotation_expiry_and_cookie_flags(self):
        login = await self.login()
        for decision in ("approve", "reject"):
            result = await self.client.post(
                f"/approvals/not-a-uuid/{decision}",
                json={"run_id": "00000000-0000-0000-0000-000000000001", "payload_digest": "0" * 64},
            )
            self.assertEqual(result.status_code, 422)
        cookie = login.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertIn("max-age=3600", cookie)
        old = self.client.cookies.get(COOKIE)
        await self.login()
        self.assertEqual(
            (await self.client.get("/session", headers={"Cookie": f"{COOKIE}={old}"})).status_code,
            401,
        )
        self.assertEqual((await self.client.delete("/session")).status_code, 200)
        self.assertEqual((await self.client.get("/session")).status_code, 401)
        await self.login()
        now = app.state.sessions.clock()
        app.state.sessions.clock = lambda: now + 3601
        self.assertEqual((await self.client.get("/session")).status_code, 401)

    async def test_seed_roles_are_server_owned_and_bad_credentials_generic(self):
        for user in PASSWORDS:
            result = await self.login(user)
            self.assertEqual(result.json()["user_id"], user)
            self.assertEqual(
                result.json()["role"], "support" if user.startswith("support") else "member"
            )
            extra = await self.client.post(
                "/session", json={"username": user, "password": PASSWORDS[user], "role": "support"}
            )
            self.assertEqual(extra.status_code, 422)
        failures = []
        for user in ("user-alpha", "unknown"):
            r = await self.client.post("/session", json={"username": user, "password": "wrong"})
            failures.append((r.status_code, r.json()))
        self.assertEqual(failures[0], failures[1])
        self.assertEqual(failures[0][0], 401)

    async def test_memory_runs_are_per_subject_and_tasks_capture_owner(self):
        await self.login()
        created = await self.client.post("/runs", json={"question": "ws-alpha 429"})
        self.assertEqual(created.status_code, 202)
        run_id = created.json()["id"]
        await self.login("user-beta")
        self.assertEqual((await self.client.get(f"/runs/{run_id}")).status_code, 404)
        await self.login("support-alpha")
        self.assertEqual((await self.client.get(f"/runs/{run_id}")).status_code, 404)
        await self.login()
        self.assertEqual((await self.client.get(f"/runs/{run_id}")).status_code, 200)
        self.assertEqual(app.state.runs.context.user_id, "user-alpha")

    async def test_unconfigured_login_fails_closed_and_attempts_bounded(self):
        app.state.sessions.passwords = {}
        self.assertEqual(
            (
                await self.client.post(
                    "/session", json={"username": "user-alpha", "password": "wrong"}
                )
            ).status_code,
            503,
        )
        self.assertEqual((await self.client.post("/runs", json={"question": "x"})).status_code, 401)
        app.state.sessions.passwords = {"user-alpha": app.state.sessions.dummy}
        for _ in range(20):
            app.state.sessions.attempts.append(app.state.sessions.clock())
        self.assertEqual(
            (
                await self.client.post(
                    "/session", json={"username": "user-alpha", "password": "wrong"}
                )
            ).status_code,
            429,
        )
