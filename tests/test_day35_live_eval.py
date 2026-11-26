"""Test the explicitly dispatched evaluation contract without a live provider."""

import unittest
from unittest.mock import AsyncMock, patch

from packages.agent.models import ModelResponse, ProviderError, Usage
from scripts.evaluate_day35_live import MAX_OUTPUT_TOKENS, evaluate, prepare


class BoundedLiveEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_cases_and_exact_scoring_with_measured_usage(self):
        dataset, prompts = prepare()
        self.assertEqual(dataset["version"], "day29-v1")
        self.assertEqual(len(prompts), 3)
        self.assertTrue(
            all(
                req.max_output_tokens == MAX_OUTPUT_TOKENS and not req.tools
                for _, req, _ in prompts
            )
        )
        provider = AsyncMock()
        provider.generate.side_effect = [
            ModelResponse(
                mode="mock",
                model="test",
                text=text,
                usage=Usage(input_tokens=10, output_tokens=1, total_tokens=11),
            )
            for text in ("60/10000", "RETRY", "UNKNOWN")
        ]
        results = await evaluate(provider, prompts)
        self.assertEqual(provider.generate.await_count, 3)
        self.assertEqual([row["passed"] for row in results], [True, False, True])
        self.assertEqual(results[0]["usage"]["total_tokens"], 11)
        self.assertTrue(all("answer" not in row for row in results))

    def test_prompt_budget_is_checked_before_provider_construction(self):
        with patch("scripts.evaluate_day35_live.MAX_PROMPT_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "byte budget"):
                prepare()

    async def test_provider_failure_stops_even_when_retryable(self):
        _, prompts = prepare()
        provider = AsyncMock()
        provider.generate.side_effect = ProviderError("rate_limit", retryable=True)
        results = await evaluate(provider, prompts)
        self.assertEqual(provider.generate.await_count, 1)
        self.assertEqual(results[0]["error"], "rate_limit")
        self.assertIsNone(results[0]["usage"])
        self.assertFalse(results[0]["passed"])
