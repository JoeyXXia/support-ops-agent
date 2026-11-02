import asyncio
import json
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from packages.agent.loop import AgentLoop
from packages.agent.models import ModelResponse, ProviderError, ToolCall
from packages.agent.providers import OpenAIProvider
from packages.agent.scenario_mock import SupportScenarioMock
from packages.tools.executor import ToolExecutor
from packages.tools.schemas import AuthContext


def call(call_id="one", name="get_workspace_usage", workspace="ws-alpha"):
    return ToolCall(call_id=call_id, name=name, arguments={"workspace_id": workspace})


def output(*calls, text=""):
    return ModelResponse(text=text, tool_calls=list(calls), model="test", mode="mock")


class Script:
    def __init__(self, outputs):
        self.outputs, self.requests = list(outputs), []

    async def generate(self, request):
        self.requests.append(request)
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class LoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.executor = ToolExecutor()
        self.context = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")

    async def run_loop(self, provider, **options):
        return await AgentLoop(provider, self.executor, **options).run(
            "ws-alpha 429", context=self.context
        )

    async def test_scenario_determinism(self):
        a, b = (
            await self.run_loop(SupportScenarioMock()),
            await self.run_loop(SupportScenarioMock()),
        )
        self.assertEqual(a, b)
        self.assertEqual((a.status, a.model_calls, a.tool_calls), ("completed", 2, 2))
        self.assertIn("60/60", a.answer)
        self.assertIn("20秒", a.answer)

    async def test_message_pairing_and_sequential_execution(self):
        provider = Script(
            [output(call("one"), call("two", "get_recent_errors")), output(text="done")]
        )
        active, peak = 0, 0
        execute = self.executor.execute

        def tracked(*args, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            time.sleep(0.005)
            result = execute(*args, **kwargs)
            active -= 1
            return result

        with patch.object(self.executor, "execute", side_effect=tracked):
            result = await self.run_loop(provider)
        self.assertEqual(result.status, "completed")
        self.assertEqual(peak, 1)
        messages = provider.requests[1].messages
        calls = [m for m in messages if isinstance(m, dict) and m.get("type") == "function_call"]
        replies = [
            m for m in messages if isinstance(m, dict) and m.get("type") == "function_call_output"
        ]
        self.assertEqual([m["call_id"] for m in calls], [m["call_id"] for m in replies])
        self.assertTrue(all(json.loads(m["output"])["ok"] for m in replies))

    async def test_unknown_tool_stops(self):
        result = await self.run_loop(Script([output(call(name="missing"))]))
        self.assertEqual(result.reason, "tool_unknown_tool")
        self.assertEqual(result.model_calls, 1)

    async def test_invalid_and_unauthorized_arguments(self):
        for c, reason in [
            (call(workspace="ws-beta"), "tool_not_found"),
            (
                ToolCall(call_id="bad", name="get_workspace_usage", arguments={"workspace_id": 3}),
                "tool_invalid_arguments",
            ),
        ]:
            self.assertEqual((await self.run_loop(Script([output(c)]))).reason, reason)

    async def test_invalid_json_before_execution(self):
        raw = NS(
            status="completed",
            output=[
                NS(
                    type="function_call",
                    arguments="{broken",
                    name="get_workspace_usage",
                    call_id="bad",
                )
            ],
            _request_id="req-bad",
        )
        client = NS(responses=NS(create=AsyncMock(return_value=raw)))
        provider = OpenAIProvider("test", api_key="test-only", client=client)
        with patch.object(self.executor, "execute") as execute:
            result = await self.run_loop(provider)
            execute.assert_not_called()
        self.assertEqual(result.reason, "invalid_output")

    async def test_no_progress(self):
        result = await self.run_loop(Script([output(call("first")), output(call("second"))]))
        self.assertEqual((result.reason, result.tool_calls), ("no_progress", 1))

    async def test_duplicate_call_ids_rejected_before_execution(self):
        with patch.object(self.executor, "execute") as execute:
            result = await self.run_loop(Script([output(call(), call(name="get_recent_errors"))]))
            execute.assert_not_called()
        self.assertEqual(result.reason, "duplicate_call_id")

    async def test_tool_budget_rejects_whole_batch(self):
        result = await self.run_loop(
            Script([output(call("one"), call("two", "get_recent_errors"))]), max_tool_calls=1
        )
        self.assertEqual((result.reason, result.tool_calls), ("tool_budget", 0))

    async def test_step_budget(self):
        result = await self.run_loop(Script([output(call())]), max_steps=1)
        self.assertEqual((result.reason, result.model_calls), ("step_budget", 1))
        with self.assertRaises(ValueError):
            AgentLoop(SupportScenarioMock(), self.executor, max_steps=7)

    async def test_model_timeout_and_cancellation(self):
        provider = NS(generate=AsyncMock(side_effect=lambda request: None))

        async def slow(request):
            await asyncio.sleep(1)

        provider.generate = slow
        result = await self.run_loop(provider, step_timeout=0.01)
        self.assertEqual(result.reason, "model_timeout")
        task = asyncio.create_task(self.run_loop(provider))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_tool_timeout(self):
        def slow(*args, **kwargs):
            time.sleep(0.04)

        with patch.object(self.executor, "execute", side_effect=slow):
            result = await self.run_loop(Script([output(call())]), step_timeout=0.01)
        self.assertEqual(result.reason, "tool_timeout")

    async def test_empty_output_and_provider_failure(self):
        self.assertEqual((await self.run_loop(Script([output()]))).reason, "empty_output")
        self.assertEqual(
            (await self.run_loop(Script([ProviderError("quota_exhausted")]))).reason,
            "quota_exhausted",
        )

    async def test_openai_stateless_output_roundtrip(self):
        reasoning = {
            "type": "reasoning",
            "id": "rs-test",
            "summary": [],
            "encrypted_content": "test-ciphertext",
        }
        function = {
            "type": "function_call",
            "call_id": "sdk-call",
            "name": "get_workspace_usage",
            "arguments": '{"workspace_id":"ws-alpha"}',
        }
        first = NS(
            status="completed",
            _request_id="req-1",
            usage=None,
            output=[
                NS(type="reasoning", model_dump=lambda **kw: reasoning),
                NS(
                    type="function_call",
                    call_id="sdk-call",
                    name="get_workspace_usage",
                    arguments=function["arguments"],
                    model_dump=lambda **kw: function,
                ),
            ],
        )
        message = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "done"}],
        }
        last = NS(
            status="completed",
            _request_id="req-2",
            usage=None,
            output=[
                NS(
                    type="message",
                    content=[NS(type="output_text", text="done")],
                    model_dump=lambda **kw: message,
                )
            ],
        )
        create = AsyncMock(side_effect=[first, last])
        provider = OpenAIProvider(
            "test", api_key="test-only", client=NS(responses=NS(create=create))
        )
        result = await self.run_loop(provider)
        self.assertEqual(result.answer, "done")
        second_input = create.call_args_list[1].kwargs["input"]
        self.assertIn(reasoning, second_input)
        self.assertIn(function, second_input)
        reply = next(item for item in second_input if item.get("type") == "function_call_output")
        self.assertEqual(reply["call_id"], "sdk-call")
        self.assertTrue(json.loads(reply["output"])["ok"])
