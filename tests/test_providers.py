import asyncio
import unittest
from types import SimpleNamespace as NS

import httpx2
import openai

from packages.agent.models import Message, ModelRequest, ProviderError
from packages.agent.providers import MockProvider, OpenAIProvider, normalize


def response(output=None, status="completed"):
    return NS(
        status=status,
        output=output or [NS(type="message", content=[NS(type="output_text", text="OK")])],
        usage=NS(input_tokens=4, output_tokens=1, total_tokens=5),
        _request_id="req-test",
    )


class Client:
    def __init__(self, result=None, error=None, delay=0):
        self.responses = self
        self.result, self.error, self.delay = result, error, delay
        self.calls = 0
        self.kwargs = None

    async def create(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


class ProvidersTest(unittest.IsolatedAsyncioTestCase):
    async def test_mock_deterministic(self):
        request = ModelRequest(messages=[Message(role="user", content="429")])
        mock = MockProvider()
        a, b = await mock.generate(request), await mock.generate(request)
        self.assertEqual(a, b)
        self.assertEqual(a.mode, "mock")
        self.assertIsNone(a.usage)

    async def test_success_and_request_options(self):
        client = Client(result=response())
        provider = OpenAIProvider("test-model", api_key="test-only", client=client)
        result = await provider.generate(
            ModelRequest(messages=[Message(role="user", content="OK")])
        )
        self.assertEqual(result.text, "OK")
        self.assertEqual(result.usage.total_tokens, 5)
        self.assertEqual(result.request_id, "req-test")
        self.assertFalse(client.kwargs["store"])
        self.assertEqual(client.calls, 1)

    async def test_timeout(self):
        client = Client(result=response(), delay=0.1)
        provider = OpenAIProvider("test-model", api_key="test-only", timeout=0.01, client=client)
        with self.assertRaises(ProviderError) as caught:
            await provider.generate(ModelRequest(messages=[Message(role="user", content="OK")]))
        self.assertEqual(caught.exception.code, "timeout")
        self.assertEqual(client.calls, 1)

    async def test_http_failures(self):
        for status, body, expected, retryable in [
            (429, {"code": "rate_limit_exceeded"}, "rate_limit", True),
            (429, {"code": "insufficient_quota"}, "quota_exhausted", False),
            (
                429,
                {"code": "credit_balance_exhausted", "type": "insufficient_quota"},
                "quota_exhausted",
                False,
            ),
            (429, {"error": {"type": "insufficient_quota"}}, "quota_exhausted", False),
            (401, {}, "authentication", False),
            (403, {}, "permission", False),
            (500, {}, "upstream", True),
        ]:
            with self.subTest(status=status, body=body):
                http = httpx2.Response(
                    status,
                    request=httpx2.Request("POST", "https://api.openai.com/v1/responses"),
                    headers={"x-request-id": "req-error"},
                )
                cls = openai.RateLimitError if status == 429 else openai.APIStatusError
                client = Client(error=cls("do-not-log-this-body", response=http, body=body))
                provider = OpenAIProvider("test-model", api_key="test-only", client=client)
                with self.assertRaises(ProviderError) as caught:
                    await provider.generate(
                        ModelRequest(messages=[Message(role="user", content="OK")])
                    )
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(caught.exception.retryable, retryable)
                self.assertEqual(caught.exception.request_id, "req-error")
                self.assertNotIn("do-not-log", str(caught.exception))
                self.assertEqual(client.calls, 1)

    def test_configuration(self):
        with self.assertRaises(ProviderError) as caught:
            OpenAIProvider("test-model", api_key="")
        self.assertEqual(caught.exception.code, "configuration")

    def test_tool_calls(self):
        item = NS(
            type="function_call",
            call_id="call-1",
            name="read_usage",
            arguments='{"workspace_id":"ws-alpha"}',
        )
        result = normalize(response([item]), "test-model")
        self.assertEqual(result.tool_calls[0].arguments, {"workspace_id": "ws-alpha"})
        for invalid in ("not json", "[]"):
            item.arguments = invalid
            with self.assertRaises(ProviderError) as caught:
                normalize(response([item]), "test-model")
            self.assertEqual(caught.exception.code, "invalid_output")

    def test_incomplete_and_unknown_output(self):
        for value in (
            response(status="incomplete"),
            response([NS(type="unknown")]),
            NS(status="completed", output=None),
        ):
            with self.assertRaises(ProviderError):
                normalize(value, "test-model")


if __name__ == "__main__":
    unittest.main()
