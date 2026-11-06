import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx2
from openai import APITimeoutError, RateLimitError

from packages.knowledge.embeddings import (
    DIMENSIONS,
    MODEL,
    EmbeddingError,
    MockEmbedding,
    OpenAIEmbedding,
    validate_vectors,
)


def response(indexes=(0,)):
    return NS(
        model=MODEL,
        data=[NS(index=i, embedding=[1.0] * DIMENSIONS) for i in indexes],
        usage=NS(total_tokens=3),
        _request_id="test-request",
    )


class EmbeddingTests(unittest.TestCase):
    def provider(self, values):
        create = Mock(side_effect=values)
        provider = OpenAIEmbedding(client=NS(embeddings=NS(create=create)), sleep=lambda _: None)
        return provider, create

    def test_out_of_order_indexes(self):
        provider, create = self.provider([response((1, 0))])
        result = provider.embed(["a", "b"])
        self.assertEqual(len(result.vectors), 2)
        self.assertEqual(result.usage_tokens, 3)
        self.assertEqual(create.call_args.kwargs["dimensions"], DIMENSIONS)

    def test_invalid_vectors_and_response(self):
        cases = [response((0, 0)), response((2,)), response(())]
        malformed = response()
        malformed.data[0].embedding = [0.0] * DIMENSIONS
        cases.append(malformed)
        for bad in cases:
            provider, create = self.provider([bad])
            with self.assertRaises(EmbeddingError) as caught:
                provider.embed(["a"])
            self.assertEqual(caught.exception.code, "invalid_output")
            self.assertEqual(create.call_count, 1)
        for vec in (
            [1.0],
            [float("nan")] * DIMENSIONS,
            [float("inf")] * DIMENSIONS,
            [True] * DIMENSIONS,
        ):
            with self.assertRaises(EmbeddingError):
                validate_vectors([vec], 1)

    def test_input_limits(self):
        provider, create = self.provider([])
        for texts in ([], [""], ["x"] * 17, ["中" * 3000]):
            with self.assertRaises(EmbeddingError):
                provider.embed(texts)
        create.assert_not_called()

    def test_retry_then_success_and_exhaustion(self):
        error = APITimeoutError(
            request=httpx2.Request("POST", "https://api.openai.com/v1/embeddings")
        )
        provider, create = self.provider([error, response()])
        self.assertEqual(provider.embed(["a"]).attempts, 2)
        provider, create = self.provider([error, error, error])
        with self.assertRaises(EmbeddingError):
            provider.embed(["a"])
        self.assertEqual(create.call_count, 3)

    def test_quota_never_retried(self):
        request = httpx2.Request("POST", "https://api.openai.com/v1/embeddings")
        error = RateLimitError(
            "test",
            response=httpx2.Response(429, request=request),
            body={"code": "credit_balance_exhausted"},
        )
        provider, create = self.provider([error])
        with self.assertRaises(EmbeddingError) as caught:
            provider.embed(["a"])
        self.assertEqual(caught.exception.code, "quota_exhausted")
        self.assertEqual(create.call_count, 1)

    def test_mock_is_deterministic_and_separate(self):
        provider = MockEmbedding()
        self.assertEqual(provider.embed(["a"]), provider.embed(["a"]))
        self.assertIsNone(provider.embed(["a"]).usage_tokens)
        self.assertNotEqual(provider.model, MODEL)
