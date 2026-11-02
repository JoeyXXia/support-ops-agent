import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from packages.tools.executor import ToolExecutor
from packages.tools.schemas import AuthContext


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.executor = ToolExecutor()
        self.alpha = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")
        self.beta = AuthContext(user_id="user-beta", tenant_id="tenant-beta", role="member")

    def call(self, tool="get_workspace_usage", args=None, context=None):
        return self.executor.execute(
            tool,
            args if args is not None else {"workspace_id": "ws-alpha"},
            context=context or self.alpha,
        )

    def test_usage_matches_facts(self):
        result = self.call()
        self.assertTrue(result.ok)
        self.assertEqual(result.data.minute_limit, 60)
        self.assertEqual(result.data.monthly_requests, 8000)
        self.assertEqual(result, self.call())

    def test_tenant_denial_matches_unknown(self):
        for name in ("get_workspace_usage", "get_recent_errors"):
            foreign = self.call(name, {"workspace_id": "ws-beta"})
            missing = self.call(name, {"workspace_id": "ws-unknown"})
            self.assertEqual(foreign, missing)
            self.assertFalse(foreign.ok)
            self.assertIsNone(foreign.data)
            self.assertEqual(foreign.error.code, "not_found")

    def test_bad_arguments_never_reach_business(self):
        for args in (
            {},
            {"workspace_id": 12},
            {"workspace_id": "../secrets"},
            {"workspace_id": "ws-alpha", "tenant_id": "tenant-beta"},
            {"workspace_id": "ws-alpha", "role": "support"},
            [],
        ):
            with self.subTest(args=args), patch.object(self.executor, "_usage") as handler:
                self.assertEqual(self.call(args=args).error.code, "invalid_arguments")
                handler.assert_not_called()

    def test_limit_strictness(self):
        for limit in (0, 21, True, "2", 2.5):
            with patch.object(self.executor, "_errors") as handler:
                result = self.call(
                    "get_recent_errors", {"workspace_id": "ws-alpha", "limit": limit}
                )
                self.assertEqual(result.error.code, "invalid_arguments")
                handler.assert_not_called()

    def test_errors_order_limit_and_version(self):
        result = self.call("get_recent_errors", {"workspace_id": "ws-alpha", "limit": 2})
        self.assertEqual(
            [event.id for event in result.data.events], ["ws-alpha-error-3", "ws-alpha-error-2"]
        )
        beta = self.call("get_recent_errors", {"workspace_id": "ws-beta"}, self.beta)
        self.assertTrue(beta.ok)
        self.assertTrue(
            all(
                event.reason is None and event.retry_after_seconds is None
                for event in beta.data.events
            )
        )

    def test_identity_not_accepted_from_dictionary(self):
        result = self.executor.execute(
            "get_workspace_usage", {"workspace_id": "ws-alpha"}, context={"user_id": "user-alpha"}
        )
        self.assertEqual(result.error.code, "forbidden")

    def test_role_mismatch_and_unknown_user(self):
        for identity in (
            AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="support"),
            AuthContext(user_id="missing", tenant_id="tenant-alpha", role="member"),
        ):
            self.assertEqual(self.call(context=identity).error.code, "forbidden")

    def test_support_requires_explicit_assignment(self):
        denied = AuthContext(user_id="support-alpha", tenant_id="tenant-alpha", role="support")
        assigned = denied.model_copy(update={"assigned_tenant_ids": ("tenant-alpha",)})
        self.assertFalse(self.call(context=denied).ok)
        self.assertTrue(self.call(context=assigned).ok)
        self.assertFalse(self.call(args={"workspace_id": "ws-beta"}, context=assigned).ok)

    def test_unauthorized_never_reads_events(self):
        with patch.object(self.executor, "_errors") as handler:
            self.assertFalse(self.call("get_recent_errors", {"workspace_id": "ws-beta"}).ok)
            handler.assert_not_called()

    def test_unknown_tool(self):
        self.assertEqual(self.call("create_ticket").error.code, "unknown_tool")

    def test_registry_schema_excludes_identity(self):
        definitions = self.executor.definitions()
        self.assertEqual(len(definitions), 2)
        for item in definitions:
            self.assertFalse(item.parameters["additionalProperties"])
            self.assertNotIn("tenant_id", item.parameters["properties"])
            self.assertNotIn("user_id", item.parameters["properties"])

    def test_empty_and_corrupt_event_data(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "demo"
            shutil.copytree(self.executor.data_dir, data)
            executor = ToolExecutor(data)
            path = data / "recent_errors.json"
            path.write_text("[]", encoding="utf-8")
            result = executor.execute(
                "get_recent_errors", {"workspace_id": "ws-alpha"}, context=self.alpha
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.data.events, [])
            path.write_text(json.dumps({"broken": True}), encoding="utf-8")
            result = executor.execute(
                "get_recent_errors", {"workspace_id": "ws-alpha"}, context=self.alpha
            )
            self.assertEqual(result.error.code, "internal_error")
            self.assertIsNone(result.data)


if __name__ == "__main__":
    unittest.main()
