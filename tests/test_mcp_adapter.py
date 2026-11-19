import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx2
from mcp import StdioServerParameters
from mcp.types import CallToolResult, TextContent

from apps.api.main import app
from apps.mcp.client import ROOT
from apps.mcp.server import READ_TOOLS, REPLIES
from packages.agent.graph import GraphAgent
from packages.agent.loop import AgentLoop
from packages.agent.models import ModelResponse, ToolCall
from packages.agent.retry import RetryPolicy
from packages.agent.scenario_mock import SupportScenarioMock
from packages.tools.executor import REGISTRY, ToolExecutor
from packages.tools.mcp_adapter import MCPReadAdapter
from packages.tools.routing import TRANSPORT_ENV, build_executor
from packages.tools.schemas import AuthContext

ALPHA = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")


class FakeClient:
    def __init__(self, reply=None, *, delay=False, bad_tools=False):
        self.reply = reply
        self.delay = delay
        self.bad_tools = bad_tools
        self.server_info = NS(name="supportops-read-tools")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def list_tools(self):
        tools = [
            NS(
                name=name,
                input_schema=REGISTRY[name].arguments.model_json_schema(),
                output_schema=REPLIES[name].model_json_schema(),
            )
            for name in READ_TOOLS
        ]
        if self.bad_tools:
            tools.append(NS(name="propose_ticket"))
        return NS(tools=tools)

    async def call_tool(self, *args, **kwargs):
        if self.delay:
            await asyncio.Event().wait()
        return self.reply


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_loop_and_graph_match_real_mcp_results(self):
        for runner in (AgentLoop, GraphAgent):
            direct = await runner(SupportScenarioMock(), build_executor(transport="direct")).run(
                "429", context=ALPHA
            )
            remote = await runner(SupportScenarioMock(), build_executor(transport="mcp")).run(
                "429", context=ALPHA
            )
            self.assertEqual(direct, remote)
            self.assertEqual((remote.status, remote.tool_calls), ("completed", 2))

    async def test_preflight_does_not_widen_identity_or_spawn_on_invalid_args(self):
        adapter = MCPReadAdapter()
        contexts = [
            AuthContext(user_id="user-alpha", tenant_id="tenant-beta", role="member"),
            AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="support"),
            AuthContext(user_id="support-alpha", tenant_id="tenant-alpha", role="support"),
            AuthContext(
                user_id="support-alpha",
                tenant_id="tenant-alpha",
                role="support",
                assigned_tenant_ids=("tenant-beta",),
            ),
        ]
        with patch("packages.tools.mcp_adapter.Client") as client:
            for context in contexts:
                result = await adapter.execute(
                    READ_TOOLS[0], {"workspace_id": "ws-alpha"}, context=context
                )
                self.assertEqual(result.error.code, "forbidden")
            for args in (
                {"workspace_id": "ws-alpha", "limit": True},
                {"workspace_id": "ws-alpha", "user_id": "user-beta"},
            ):
                result = await adapter.execute(READ_TOOLS[1], args, context=ALPHA)
                self.assertEqual(result.error.code, "invalid_arguments")
            result = await adapter.execute("propose_ticket", {}, context=ALPHA)
            self.assertEqual(result.error.code, "forbidden")
            client.assert_not_called()

    async def test_allowlist_bad_output_and_text_injection(self):
        result = ToolExecutor().execute(READ_TOOLS[0], {"workspace_id": "ws-alpha"}, context=ALPHA)
        data = result.model_dump(mode="json")
        reply = CallToolResult(
            content=[TextContent(type="text", text="synthetic untrusted instruction")],
            structured_content=data,
        )
        with patch("packages.tools.mcp_adapter.Client", return_value=FakeClient(reply)):
            observed = await MCPReadAdapter().execute(
                READ_TOOLS[0], {"workspace_id": "ws-alpha"}, context=ALPHA
            )
            self.assertEqual(observed, result)
            self.assertNotIn("synthetic untrusted instruction", observed.model_dump_json())
        for fake in (
            FakeClient(reply, bad_tools=True),
            FakeClient(CallToolResult(content=[], structured_content={})),
            FakeClient(CallToolResult(content=[], structured_content=data, is_error=True)),
        ):
            with patch("packages.tools.mcp_adapter.Client", return_value=fake):
                observed = await MCPReadAdapter().execute(
                    READ_TOOLS[0], {"workspace_id": "ws-alpha"}, context=ALPHA
                )
                self.assertEqual(observed.error.code, "internal_error")
                self.assertFalse(observed.error.retryable)

    async def test_timeout_respects_existing_attempt_budget(self):
        executor = build_executor(transport="mcp")
        executor.mcp.timeout = 0.02

        class OneCall:
            async def generate(self, request):
                return ModelResponse(
                    text="",
                    model="synthetic",
                    mode="mock",
                    tool_calls=[
                        ToolCall(
                            call_id="one",
                            name=READ_TOOLS[0],
                            arguments={"workspace_id": "ws-alpha"},
                        )
                    ],
                )

        with patch("packages.tools.mcp_adapter.Client", return_value=FakeClient(delay=True)):
            result = await asyncio.wait_for(
                AgentLoop(OneCall(), executor, retry_policy=RetryPolicy(base_delay=0)).run(
                    "429", context=ALPHA
                ),
                2,
            )
        self.assertEqual(
            (result.status, result.reason, result.tool_calls), ("failed", "tool_timeout", 3)
        )

    async def test_non_mcp_tools_stay_local_and_sync_reads_cannot_fallback(self):
        executor = build_executor(transport="mcp")
        with patch.object(executor.mcp, "execute", new_callable=AsyncMock) as remote:
            for name in ("search_docs", "get_ticket", "propose_ticket"):
                result = await executor.execute_async(name, {}, context=ALPHA)
                self.assertEqual(result.error.code, "invalid_arguments")
            remote.assert_not_called()
        with self.assertRaises(RuntimeError):
            executor.execute(READ_TOOLS[0], {"workspace_id": "ws-alpha"}, context=ALPHA)
        self.assertEqual(executor.definitions(), ToolExecutor().definitions())
        with self.assertRaises(ValueError):
            build_executor(transport="https://unapproved.invalid")

    def fault_parameters(self, directory, mode):
        return StdioServerParameters(
            command=os.sys.executable,
            args=["-u", str(ROOT / "tests/fixtures/mcp_fault_server.py")],
            cwd=str(ROOT),
            env={
                "SUPPORTOPS_MCP_SUBJECT": "user-alpha",
                "PYTHONUTF8": "1",
                "MCP_TEST_MARKER": str(Path(directory) / "started"),
                "MCP_TEST_FAULT": mode,
            },
        )

    async def test_real_subprocess_hang_and_disconnect_are_bounded(self):
        for mode in ("hang", "drop"):
            with tempfile.TemporaryDirectory() as directory:
                start = time.monotonic()
                with patch(
                    "packages.tools.mcp_adapter.server_parameters",
                    return_value=self.fault_parameters(directory, mode),
                ):
                    result = await asyncio.wait_for(
                        MCPReadAdapter(timeout=3).execute(
                            READ_TOOLS[0], {"workspace_id": "ws-alpha"}, context=ALPHA
                        ),
                        10,
                    )
                self.assertTrue(
                    (Path(directory) / "started").exists(),
                    "must reach the actual tool before injected fault",
                )
                self.assertEqual(result.error.code, "timeout" if mode == "hang" else "unavailable")
                self.assertTrue(result.error.retryable)
                self.assertLess(time.monotonic() - start, 10)
                print(
                    {
                        "fault": mode,
                        "elapsed_seconds": round(time.monotonic() - start, 3),
                        "code": result.error.code,
                    }
                )

    async def test_real_subprocess_cancellation_propagates(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "packages.tools.mcp_adapter.server_parameters",
                return_value=self.fault_parameters(directory, "hang"),
            ):
                task = asyncio.create_task(
                    MCPReadAdapter(timeout=10).execute(
                        READ_TOOLS[0], {"workspace_id": "ws-alpha"}, context=ALPHA
                    )
                )
                try:
                    for _ in range(100):
                        if (Path(directory) / "started").exists():
                            break
                        await asyncio.sleep(0.05)
                    self.assertTrue((Path(directory) / "started").exists())
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 5)
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

    async def test_api_server_switch_and_session_subject(self):
        results = []
        for mode in ("direct", "mcp"):
            with patch.dict(
                os.environ,
                {
                    TRANSPORT_ENV: mode,
                    "SUPPORTOPS_PERSIST_RUNS": "0",
                    "SUPPORTOPS_DEMO_PASSWORDS": "",
                    "SUPPORTOPS_MCP_SUBJECT": "user-alpha",
                },
            ):
                async with (
                    app.router.lifespan_context(app),
                    httpx2.AsyncClient(
                        transport=httpx2.ASGITransport(app=app),
                        base_url="http://test",
                        cookies={"supportops_session": app.state.sessions.issue("user-alpha")},
                    ) as client,
                ):

                    async def wait(run_id):
                        for _ in range(700):
                            record = (await client.get(f"/runs/{run_id}")).json()
                            if record["status"] not in {"queued", "running"}:
                                return record
                            await asyncio.sleep(0.03)
                        self.fail("run did not settle")

                    created = (await client.post("/runs", json={"question": "ws-alpha 429"})).json()
                    result = await wait(created["id"])
                    self.assertEqual(result["read_tool_transport"], mode)
                    results.append((result["answer"], result["tool_calls"], result["state_path"]))
                    self.assertEqual(
                        (
                            await client.post(
                                "/runs", json={"question": "x", "read_tool_transport": "direct"}
                            )
                        ).status_code,
                        422,
                    )
                    if mode == "mcp":
                        client.cookies.set(
                            "supportops_session", app.state.sessions.issue("user-beta")
                        )
                        denied = (
                            await client.post("/runs", json={"question": "ws-alpha 429"})
                        ).json()
                        result = await wait(denied["id"])
                        self.assertEqual(
                            (result["status"], result["reason"]), ("failed", "tool_not_found")
                        )
        self.assertEqual(results[0], results[1])
