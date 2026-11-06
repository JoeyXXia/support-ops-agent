"""Bounded synchronous embedding adapter; SDK retries disabled. No real calls by default."""

import hashlib
import math
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DIMENSIONS = 1536
MODEL = "text-embedding-3-small"
BATCH_SIZE = 16


class EmbeddingError(Exception):
    def __init__(self, code, *, retryable=False, request_id=None, attempts=0):
        self.code, self.retryable, self.request_id = code, retryable, request_id
        self.attempts = attempts
        super().__init__(code)


def validate_vectors(vectors, count):
    if not isinstance(vectors, list) or len(vectors) != count:
        raise EmbeddingError("invalid_output")
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != DIMENSIONS:
            raise EmbeddingError("invalid_output")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
            raise EmbeddingError("invalid_output")
        if not any(v != 0 for v in vector):
            raise EmbeddingError("invalid_output")


@dataclass
class EmbeddingBatch:
    vectors: list[list[float]]
    request_id: str | None = None
    usage_tokens: int | None = None
    attempts: int = 1


class MockEmbedding:
    provider = "mock"
    model = "fixture-embedding-v1"

    def embed(self, texts):
        # Deliberately not semantic embeddings. Separated in DB by provider and model.
        vectors = []
        for text in texts:
            seed = hashlib.sha256(text.encode()).digest()
            vector = [(seed[i % 32] - 127.5) / 128 for i in range(DIMENSIONS)]
            norm = math.sqrt(sum(v * v for v in vector))
            vectors.append([v / norm for v in vector])
        return EmbeddingBatch(vectors)


class OpenAIEmbedding:
    provider = "openai"
    model = MODEL

    def __init__(self, *, client=None, max_attempts=3, sleep=time.sleep):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be 1..3")
        self.max_attempts, self.sleep = max_attempts, sleep
        if client is None:
            from openai import OpenAI

            load_dotenv(Path(__file__).resolve().parents[2] / ".env.local", override=False)
            key = os.getenv("OPENAI_API_KEY", "")
            if not key.strip():
                raise EmbeddingError("configuration")
            client = OpenAI(
                api_key=key, timeout=20, max_retries=0, base_url="https://api.openai.com/v1"
            )
        self.client = client

    def embed(self, texts):
        from openai import APIConnectionError, APIStatusError, APITimeoutError

        if not 1 <= len(texts) <= BATCH_SIZE or any(
            not t.strip() or len(t.encode()) > 8192 for t in texts
        ):
            raise EmbeddingError("invalid_input")
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.client.embeddings.create(
                    model=self.model, input=texts, dimensions=DIMENSIONS, encoding_format="float"
                )
            except (APITimeoutError, APIConnectionError) as exc:
                error = EmbeddingError(
                    "timeout" if isinstance(exc, APITimeoutError) else "connection", retryable=True
                )
            except APIStatusError as exc:
                body = exc.body if isinstance(exc.body, dict) else {}
                error_body = body.get("error") if isinstance(body.get("error"), dict) else body
                quota = (
                    error_body.get("code") in {"insufficient_quota", "credit_balance_exhausted"}
                    or error_body.get("type") == "insufficient_quota"
                )
                code = (
                    "quota_exhausted"
                    if exc.status_code == 429 and quota
                    else {
                        400: "invalid_request",
                        401: "authentication",
                        403: "permission",
                        404: "model_not_found",
                        429: "rate_limit",
                    }.get(exc.status_code, "upstream")
                )
                error = EmbeddingError(
                    code,
                    retryable=code == "rate_limit" or exc.status_code >= 500,
                    request_id=exc.request_id,
                )
            else:
                try:
                    if response.model != self.model or len(response.data) != len(texts):
                        raise ValueError("response mismatch")
                    indexes = [item.index for item in response.data]
                    if any(type(i) is not int for i in indexes) or sorted(indexes) != list(
                        range(len(texts))
                    ):
                        raise ValueError("index mismatch")
                    vectors = [
                        item.embedding for item in sorted(response.data, key=lambda v: v.index)
                    ]
                    validate_vectors(vectors, len(texts))
                    tokens = response.usage.total_tokens if response.usage else None
                    if tokens is not None and (type(tokens) is not int or tokens < 0):
                        raise ValueError("invalid usage")
                    return EmbeddingBatch(vectors, response._request_id, tokens, attempt)
                except (ValueError, AttributeError, TypeError, EmbeddingError):
                    raise EmbeddingError(
                        "invalid_output",
                        request_id=getattr(response, "_request_id", None),
                        attempts=attempt,
                    ) from None
            if not error.retryable or attempt == self.max_attempts:
                error.attempts = attempt
                raise error from None
            self.sleep(min(2, 0.25 * 2 ** (attempt - 1)) + random.uniform(0, 0.1))

    def close(self):
        self.client.close()
