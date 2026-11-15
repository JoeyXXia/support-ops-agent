import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import OperationalError
from test_loop import Script, call, output

from packages.agent.graph import GraphAgent
from packages.agent.loop import AgentLoop
from packages.agent.models import ProviderError
from packages.agent.retry import RetryPolicy, classify_failure
from packages.tools.executor import ToolExecutor
from packages.tools.schemas import AuthContext, ToolFailure, ToolResult


class RetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")

    async def test_model_backoff_and_logical_steps_in_both_engines(self):
        for engine in (AgentLoop, GraphAgent):
            with self.subTest(engine=engine.__name__):
                provider = Script(
                    [
                        ProviderError("rate_limit", retryable=True),
                        output(call()),
                        output(text="done"),
                    ]
                )
                with patch("packages.agent.loop.asyncio.sleep", new_callable=AsyncMock) as sleep:
                    result = await engine(
                        provider, ToolExecutor(), max_steps=2, max_model_calls=3
                    ).run("429", context=self.context)
                self.assertEqual(
                    (result.status, result.steps, result.model_calls, result.tool_calls),
                    ("completed", 2, 3, 1),
                )
                self.assertEqual([c.args[0] for c in sleep.await_args_list], [0.1])

    async def test_retry_exhaustion_is_finite_and_logged(self):
        provider = Script([ProviderError("rate_limit", retryable=True) for _ in range(3)])
        with patch("packages.agent.loop.asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await asyncio.wait_for(
                AgentLoop(provider, ToolExecutor()).run("429", context=self.context), 0.5
            )
        self.assertEqual(
            (result.status, result.reason, result.model_calls), ("failed", "rate_limit", 3)
        )
        self.assertEqual([c.args[0] for c in sleep.await_args_list], [0.1, 0.2])
        self.assertEqual(result.events[-1]["kind"], "run.stopped")
        self.assertEqual(result.events[-1]["model_calls"], 3)

    async def test_permanent_provider_errors_never_retry(self):
        for code in ("permission", "authentication", "invalid_request", "quota_exhausted"):
            with self.subTest(code=code):
                provider = Script([ProviderError(code, retryable=True)])
                result = await AgentLoop(provider, ToolExecutor()).run("429", context=self.context)
                self.assertEqual((result.reason, result.model_calls), (code, 1))

    async def test_model_budget_counts_failed_attempts(self):
        for engine in (AgentLoop, GraphAgent):
            provider = Script([ProviderError("rate_limit", retryable=True) for _ in range(2)])
            result = await engine(
                provider, ToolExecutor(), max_model_calls=2, retry_policy=RetryPolicy(base_delay=0)
            ).run("429", context=self.context)
            self.assertEqual(
                (result.status, result.reason, result.model_calls), ("stopped", "model_budget", 2)
            )

    async def test_tool_retry_and_permission_stop(self):
        for code, retry, expected in (
            ("rate_limit", True, 2),
            ("forbidden", True, 1),
            ("invalid_arguments", True, 1),
        ):
            with self.subTest(code=code):
                executor = ToolExecutor()
                good = executor.execute(
                    "get_workspace_usage", {"workspace_id": "ws-alpha"}, context=self.context
                )
                bad = ToolResult(
                    tool="get_workspace_usage",
                    ok=False,
                    error=ToolFailure(code=code, message="synthetic", retryable=retry),
                )
                provider = Script([output(call()), output(text="done")])
                with patch.object(executor, "execute", side_effect=[bad, good]) as execute:
                    result = await AgentLoop(
                        provider, executor, retry_policy=RetryPolicy(base_delay=0)
                    ).run("429", context=self.context)
                self.assertEqual((execute.call_count, result.tool_calls), (expected, expected))
                self.assertEqual(result.status, "completed" if expected == 2 else "failed")

    async def test_tool_budget_and_unknown_action_not_retried(self):
        for name, budget, reason in (
            ("get_workspace_usage", 1, "tool_budget"),
            ("create_ticket", 8, "tool_rate_limit"),
        ):
            executor = ToolExecutor()
            bad = ToolResult(
                tool=name,
                ok=False,
                error=ToolFailure(code="rate_limit", message="synthetic", retryable=True),
            )
            with patch.object(executor, "execute", return_value=bad) as execute:
                result = await AgentLoop(
                    Script([output(call(name=name))]), executor, max_tool_calls=budget
                ).run("429", context=self.context)
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(result.reason, reason)

    async def test_timeout_is_bounded_and_cancellation_propagates(self):
        calls = 0

        class Slow:
            async def generate(self, request):
                nonlocal calls
                calls += 1
                await asyncio.sleep(1)

        result = await asyncio.wait_for(
            AgentLoop(
                Slow(), ToolExecutor(), step_timeout=0.001, retry_policy=RetryPolicy(base_delay=0)
            ).run("429", context=self.context),
            0.5,
        )
        self.assertEqual((result.reason, result.model_calls, calls), ("model_timeout", 3, 3))
        provider = Script([ProviderError("rate_limit", retryable=True)])
        loop = AgentLoop(provider, ToolExecutor(), retry_policy=RetryPolicy(base_delay=1))
        ready = asyncio.Event()
        task = asyncio.create_task(
            loop.run(
                "429",
                context=self.context,
                on_event=lambda e: ready.set() if e["kind"] == "retry.scheduled" else None,
            )
        )
        await ready.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(provider.requests), 1)

    async def test_policy_validation_and_legacy_state_budget(self):
        for options in (
            {"max_attempts": 4},
            {"max_attempts": True},
            {"base_delay": float("nan")},
            {"base_delay": -1},
        ):
            with self.assertRaises(ValueError):
                RetryPolicy(**options)
        graph = GraphAgent(Script([]), ToolExecutor())
        turn = graph.start("429", context=self.context, on_event=None, grounded=False)
        turn.result.model_calls = 2
        old = graph._snapshot(turn)
        del old["result"]["steps"]
        self.assertEqual(graph._restore(old).result.steps, 2)

    async def test_database_failure_classification(self):
        class DriverError(Exception):
            def __init__(self, state):
                self.sqlstate = state

        for state, retryable in (
            ("40001", True),
            ("40P01", True),
            ("08006", True),
            ("42501", False),
            ("22023", False),
        ):
            error = OperationalError(None, None, DriverError(state), connection_invalidated=True)
            self.assertEqual(classify_failure(error)[1], retryable)
