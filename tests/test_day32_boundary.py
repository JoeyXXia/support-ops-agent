"""A deterministic untrusted tool response cannot extend the authenticated read scope."""

import unittest
from unittest.mock import patch

from packages.agent.graph import GraphAgent
from packages.agent.loop import AgentLoop
from packages.agent.models import ModelResponse, ToolCall
from packages.tools.executor import ToolExecutor
from packages.tools.schemas import AuthContext

MARKER = "synthetic-untrusted-scope-suggestion"
ALPHA = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")


def respond(call):
    return ModelResponse(text="", model="day32-test", mode="mock", tool_calls=[call])


class ToolResponseBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_response_cannot_grant_foreign_workspace(self):
        class UntrustedUsage(ToolExecutor):
            def __init__(self):
                super().__init__()
                self.reads = []

            def _usage(self, args, workspace):
                self.reads.append(workspace["id"])
                trusted = super()._usage(args, workspace)
                return trusted.model_copy(update={"plan": MARKER})

        class FollowSuggestion:
            async def generate(self, request):
                outputs = [
                    item
                    for item in request.messages
                    if isinstance(item, dict) and item.get("type") == "function_call_output"
                ]
                if outputs and MARKER not in outputs[-1]["output"]:
                    raise AssertionError("The synthetic marker must reach the test provider")
                return respond(
                    ToolCall(
                        call_id="second" if outputs else "first",
                        name="get_workspace_usage",
                        arguments={"workspace_id": "ws-beta" if outputs else "ws-alpha"},
                    )
                )

        executor = UntrustedUsage()
        run = await GraphAgent(FollowSuggestion(), executor).run(
            "synthetic boundary", context=ALPHA
        )
        self.assertEqual((run.status, run.reason, run.tool_calls), ("failed", "tool_not_found", 2))
        self.assertEqual(executor.reads, ["ws-alpha"])
        self.assertEqual((run.write_calls, run.ticket), (0, None))
        self.assertIsNone(run.events[-1]["result"]["data"])

    async def test_restored_grounded_batch_is_checked_at_execution(self):
        executor = ToolExecutor()
        agent = AgentLoop(None, executor)
        turn = agent.start("synthetic checkpoint", context=ALPHA, on_event=None, grounded=True)
        turn.response = respond(
            ToolCall(
                call_id="preexisting-call",
                name="propose_ticket",
                arguments={"workspace_id": "ws-alpha", "title": "合成", "description": "回归"},
            )
        )
        with patch.object(executor, "execute_async") as execute:
            self.assertFalse(await agent.call_tools(turn))
            execute.assert_not_awaited()
        self.assertEqual((turn.result.status, turn.result.reason), ("failed", "tool_forbidden"))
        self.assertEqual(turn.result.tool_calls, 0)
