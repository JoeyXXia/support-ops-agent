"""Bounded retries for explicitly permitted operations."""

import math
from dataclasses import dataclass

from sqlalchemy.exc import DBAPIError

from .models import ProviderError

TRANSIENT_CODES = frozenset({"timeout", "rate_limit", "connection", "upstream", "unavailable"})
RETRYABLE_TOOLS = frozenset(
    {"get_workspace_usage", "get_recent_errors", "search_docs", "get_ticket", "propose_ticket"}
)


class OperationFailure(Exception):
    def __init__(self, code, *, retryable=False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.1

    def __post_init__(self):
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if (
            type(self.base_delay) not in {int, float}
            or not math.isfinite(self.base_delay)
            or not 0 <= self.base_delay <= 1
        ):
            raise ValueError("base_delay must be finite and between 0 and 1")

    def delay(self, attempt):
        return self.base_delay * 2 ** (attempt - 1)


def classify_failure(error):
    if isinstance(error, (ProviderError, OperationFailure)):
        return error.code, error.retryable and error.code in TRANSIENT_CODES
    if isinstance(error, TimeoutError):
        return "timeout", True
    if isinstance(error, DBAPIError):
        state = getattr(error.orig, "sqlstate", "") or ""
        if state == "42501":
            return "forbidden", False
        if state.startswith("22"):
            return "invalid_arguments", False
        if (
            state in {"40001", "40P01"}
            or state.startswith("08")
            or (not state and error.connection_invalidated)
        ):
            return "unavailable", True
    return "internal_error", False
