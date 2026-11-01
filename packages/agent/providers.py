import asyncio
import json
import logging
import os
from pathlib import Path

from pydantic import ValidationError

from .models import ModelRequest, ModelResponse, ProviderError, ToolCall, Usage

logger = logging.getLogger(__name__)


class MockProvider:
    """Deterministic fixture response, never presented as model-generated."""

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text="[mock] 请先提供工作区、版本和用量；仅凭429不能确定原因。",
            model="deterministic-mock-v1",
            mode="mock",
            request_id="mock-request-001",
        )


def normalize(response, model: str) -> ModelResponse:
    try:
        if response.status != "completed":
            raise ValueError("incomplete response")
        calls = []
        texts = []
        for item in response.output:
            if item.type == "function_call":
                arguments = json.loads(item.arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be an object")
                calls.append(ToolCall(call_id=item.call_id, name=item.name, arguments=arguments))
            elif item.type == "message":
                for part in item.content:
                    if part.type == "output_text":
                        texts.append(part.text)
                    elif part.type == "refusal":
                        raise ProviderError("refusal", request_id=response._request_id)
                    else:
                        raise ValueError("unsupported content")
            elif item.type != "reasoning":
                raise ValueError("unsupported output item")
        text = "".join(texts)
        if not text.strip() and not calls:
            raise ValueError("empty output")
        raw = response.usage
        usage = (
            None
            if raw is None
            else Usage(
                input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens,
                total_tokens=raw.total_tokens,
            )
        )
        return ModelResponse(
            text=text,
            tool_calls=calls,
            usage=usage,
            request_id=response._request_id,
            model=model,
            mode="live",
        )
    except (ValueError, TypeError, AttributeError, ValidationError):
        raise ProviderError(
            "invalid_output", request_id=getattr(response, "_request_id", None)
        ) from None


class OpenAIProvider:
    def __init__(self, model: str, *, api_key: str, timeout: float = 20, client=None):
        if not api_key.strip() or not model.strip():
            raise ProviderError("configuration")
        if timeout <= 0:
            raise ProviderError("configuration")
        from openai import AsyncOpenAI

        self.model = model
        self.timeout = timeout
        self.client = (
            client
            if client is not None
            else AsyncOpenAI(
                api_key=api_key,
                timeout=timeout,
                max_retries=0,
                base_url="https://api.openai.com/v1",
            )
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

        try:
            async with asyncio.timeout(self.timeout):
                response = await self.client.responses.create(
                    model=self.model,
                    input=[m.model_dump() for m in request.messages],
                    tools=[
                        dict(type="function", strict=False, **t.model_dump()) for t in request.tools
                    ],
                    max_output_tokens=request.max_output_tokens,
                    store=False,
                )
            result = normalize(response, self.model)
            logger.info("model.finished mode=live request_id=%s", result.request_id)
            return result
        except (TimeoutError, APITimeoutError):
            raise ProviderError("timeout", retryable=True) from None
        except RateLimitError as exc:
            # Both exhausted quota and temporary rate limits use HTTP 429.
            body = exc.body if isinstance(exc.body, dict) else {}
            error = body.get("error") if isinstance(body.get("error"), dict) else body
            code = error.get("code")
            exhausted = (
                code in {"insufficient_quota", "credit_balance_exhausted"}
                or error.get("type") == "insufficient_quota"
            )
            raise ProviderError(
                "quota_exhausted" if exhausted else "rate_limit",
                retryable=not exhausted,
                request_id=exc.request_id,
            ) from None
        except APIConnectionError:
            raise ProviderError("connection", retryable=True) from None
        except APIStatusError as exc:
            code = {
                401: "authentication",
                403: "permission",
                400: "invalid_request",
                404: "model_not_found",
            }.get(exc.status_code, "upstream")
            raise ProviderError(
                code, retryable=exc.status_code >= 500, request_id=exc.request_id
            ) from None

    async def close(self):
        await self.client.close()


def from_environment() -> OpenAIProvider:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env.local", override=False)
    return OpenAIProvider(
        os.getenv("OPENAI_MODEL", "gpt-4.1-mini"), api_key=os.getenv("OPENAI_API_KEY", "")
    )
