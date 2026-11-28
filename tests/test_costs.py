"""Accounting uses synthetic prices and an injected provider response, without network I/O."""

import json
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock

from packages.agent.costs import Catalog, CostPolicy
from packages.agent.graph import GraphAgent
from packages.agent.loop import AgentLoop
from packages.agent.models import ModelResponse, ProviderError, ToolCall, Usage
from packages.tools.executor import ToolExecutor
from packages.tools.schemas import AuthContext

CONTEXT = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")
CATALOG = Catalog.model_validate(
    {
        "currency": "USD",
        "prices": [
            {
                "model": "fixture",
                "effective_on": "2026-01-01",
                "input_per_million": "1000",
                "output_per_million": "2000",
            },
            {
                "model": "fixture",
                "effective_on": "2027-01-01",
                "input_per_million": "9999",
                "output_per_million": "9999",
            },
        ],
    }
)


class CostTests(unittest.IsolatedAsyncioTestCase):
    async def test_response_usage_provenance_and_dated_estimate(self):
        for mode, source in (("live", "provider_reported"), ("mock", "mock_fixture")):
            with self.subTest(mode=mode):
                provider = AsyncMock()
                provider.generate.return_value = ModelResponse(
                    text="fixture answer",
                    model="fixture",
                    mode=mode,
                    usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
                )
                policy = CostPolicy.model_validate({"catalog": CATALOG, "as_of": "2026-11-28"})
                result = await AgentLoop(provider, ToolExecutor(), cost_policy=policy).run(
                    "fixture", context=CONTEXT
                )
                amount = result.accounting.estimated_total_usd
                self.assertEqual(amount, Decimal("0.02"))
                self.assertEqual(result.accounting.calls[0].usage_source, source)
                serialized = json.loads(json.dumps(result.model_dump()))["accounting"]
                self.assertEqual(serialized["calls"][0]["effective_on"], "2026-01-01")
                self.assertEqual(serialized["calls"][0]["usage"]["input_tokens"], 10)
                self.assertEqual(serialized["calls"][0]["usage"]["output_tokens"], 5)

    async def test_budget_stops_before_requested_tool_and_missing_cost_is_unknown(self):
        for priced, reason in ((True, "cost_budget"), (False, "cost_unknown")):
            with self.subTest(priced=priced):
                provider = AsyncMock()
                provider.generate.return_value = ModelResponse(
                    text="",
                    model="fixture",
                    mode="mock",
                    usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
                    tool_calls=[
                        ToolCall(
                            call_id="usage",
                            name="get_workspace_usage",
                            arguments={"workspace_id": "ws-alpha"},
                        )
                    ],
                )
                policy = CostPolicy.model_validate(
                    {
                        "catalog": CATALOG if priced else Catalog(),
                        "budget_usd": "0.02",
                        "as_of": "2026-11-28",
                    }
                )
                agent = GraphAgent(provider, ToolExecutor(), cost_policy=policy)
                result = await agent.run("fixture", context=CONTEXT)
                self.assertEqual(
                    (result.status, result.reason, result.tool_calls), ("stopped", reason, 0)
                )
                self.assertEqual(provider.generate.await_count, 1)
                if not priced:
                    self.assertIsNone(result.accounting.estimated_total_usd)
                    self.assertIsNone(result.accounting.calls[0].estimated_usd)

    async def test_capped_provider_error_does_not_retry_unpriced_attempt(self):
        provider = AsyncMock()
        provider.generate.side_effect = ProviderError("rate_limit", retryable=True)
        policy = CostPolicy(budget_usd=Decimal("0.02"))
        result = await AgentLoop(provider, ToolExecutor(), cost_policy=policy).run(
            "fixture", context=CONTEXT
        )
        self.assertEqual(provider.generate.await_count, 1)
        self.assertEqual(result.reason, "cost_unknown")
        self.assertEqual(result.accounting.calls[0].usage_source, "unavailable")
