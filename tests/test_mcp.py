import asyncio
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from mcp import Client, MCPError
from mcp.client.stdio import get_default_environment
from mcp.types import INVALID_PARAMS

from apps.mcp.client import ROOT, server_parameters, smoke
from apps.mcp.server import READ_TOOLS, SUBJECT_ENV, create_server, subject_context
from packages.tools.executor import ToolExecutor


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_stdio_contract_for_both_tenants(self):
        for subject, workspace in (("user-alpha", "ws-alpha"), ("user-beta", "ws-beta")):
            result = await smoke(subject)
            self.assertEqual(result["transport"], "real child process stdio")
            self.assertTrue(result["client_context_closed"])
            self.assertTrue(result["protocol_version"])
            self.assertEqual(result["server"]["name"], "supportops-read-tools")
            self.assertEqual(result["observations"][0]["result"]["data"]["workspace_id"], workspace)
            self.assertEqual(result["observations"][-1]["protocol_error"], INVALID_PARAMS)

    async def test_discovery_has_exact_schemas_annotations_and_no_writes(self):
        async with Client(create_server("user-alpha")) as client:
            tools = (await client.list_tools()).tools
            self.assertEqual([t.name for t in tools], list(READ_TOOLS))
            for tool in tools:
                self.assertFalse(tool.input_schema["additionalProperties"])
                self.assertNotIn("user_id", tool.input_schema["properties"])
                self.assertNotIn("tenant_id", tool.input_schema["properties"])
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertTrue(tool.annotations.idempotent_hint)
                self.assertFalse(tool.annotations.open_world_hint)
                reply = await client.call_tool(tool.name, {"workspace_id": "ws-alpha"})
                self.assertFalse(reply.is_error)
                Draft202012Validator(tool.output_schema).validate(reply.structured_content)
                self.assertEqual(json.loads(reply.content[0].text), reply.structured_content)
            for name in ("propose_ticket", "get_ticket", "search_docs", "create_ticket", "unknown"):
                with self.assertRaises(MCPError) as caught:
                    await client.call_tool(name, {"workspace_id": "ws-alpha"})
                self.assertEqual(caught.exception.error.code, INVALID_PARAMS)

    async def test_invalid_arguments_remain_strict_and_do_not_change_principal(self):
        async with Client(create_server("user-alpha")) as client:
            for arguments in (
                {"workspace_id": "ws-alpha", "limit": True},
                {"workspace_id": "ws-alpha", "limit": "2"},
                {"workspace_id": "ws-alpha", "limit": 0},
                {"workspace_id": "ws-alpha", "role": "support"},
                {"workspace_id": "ws-beta", "tenant_id": "tenant-beta"},
            ):
                reply = await client.call_tool("get_recent_errors", arguments)
                self.assertTrue(reply.is_error)
                self.assertEqual(reply.structured_content["error"]["code"], "invalid_arguments")
            denied = await client.call_tool(
                "get_workspace_usage",
                {"workspace_id": "ws-beta"},
                meta={"user_id": "user-beta", "tenant_id": "tenant-beta"},
            )
            missing = await client.call_tool("get_workspace_usage", {"workspace_id": "ws-missing"})
            self.assertEqual(denied.structured_content, missing.structured_content)
            self.assertEqual(denied.structured_content["error"]["code"], "not_found")

    async def test_support_subject_has_only_its_server_assigned_tenant(self):
        executor = ToolExecutor()
        context = subject_context("support-alpha", executor)
        self.assertEqual(context.assigned_tenant_ids, ("tenant-alpha",))
        async with Client(create_server("support-alpha")) as client:
            own = await client.call_tool("get_workspace_usage", {"workspace_id": "ws-alpha"})
            denied = await client.call_tool("get_workspace_usage", {"workspace_id": "ws-beta"})
            self.assertFalse(own.is_error)
            self.assertTrue(denied.is_error)

    async def test_unexpected_business_failure_is_sanitized(self):
        executor = ToolExecutor()
        with patch.object(
            executor, "execute", side_effect=RuntimeError("synthetic-private-marker")
        ):
            async with Client(create_server("user-alpha", executor=executor)) as client:
                reply = await client.call_tool("get_workspace_usage", {"workspace_id": "ws-alpha"})
                self.assertTrue(reply.is_error)
                self.assertEqual(reply.structured_content["error"]["code"], "internal_error")
                self.assertNotIn("synthetic-private-marker", reply.model_dump_json())

    async def test_missing_or_unknown_startup_subject_fails_without_stdout(self):
        for subject in (None, "not-a-seed-user"):
            env = get_default_environment()
            env["PYTHONUTF8"] = "1"
            env.pop(SUBJECT_ENV, None)
            if subject:
                env[SUBJECT_ENV] = subject
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-u", "-m", "apps.mcp.server"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("Configure SUPPORTOPS_MCP_SUBJECT", result.stderr)

    async def test_launcher_is_fixed_and_does_not_forward_application_secrets(self):
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "synthetic-unused-key", "DATABASE_URL": "synthetic-not-a-url"},
        ):
            parameters = server_parameters("user-alpha")
        self.assertEqual(parameters.command, sys.executable)
        self.assertEqual(parameters.args, ["-u", "-m", "apps.mcp.server"])
        self.assertEqual(parameters.cwd, str(ROOT))
        self.assertEqual(set(parameters.env), {SUBJECT_ENV, "PYTHONUTF8"})
        with self.assertRaises(ValueError):
            server_parameters("user-alpha; arbitrary-command")
