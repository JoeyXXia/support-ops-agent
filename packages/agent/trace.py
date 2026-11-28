"""Allowlisted operational trace fields. Business payloads stay in run records."""

import hashlib
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from uuid import UUID

from .retry import RETRYABLE_TOOLS, classify_failure

REQUEST_ID = ContextVar("supportops_request_id", default=None)
RUN_ID = ContextVar("supportops_run_id", default=None)
LOGGER = logging.getLogger("supportops.trace")
CODES = frozenset(
    {
        "timeout",
        "model_timeout",
        "write_timeout",
        "rate_limit",
        "connection",
        "upstream",
        "unavailable",
        "internal_error",
        "invalid_arguments",
        "forbidden",
        "not_found",
        "unknown_tool",
        "authentication",
        "permission",
        "configuration",
        "invalid_request",
        "model_not_found",
        "quota_exhausted",
        "invalid_output",
        "refusal",
        "approval_invalid",
        "tool_forbidden",
        "run_timeout",
        "cancelled_by_owner",
        "invalid_citations",
        "empty_output",
        "step_budget",
        "model_budget",
        "tool_budget",
        "write_budget",
        "duplicate_call_id",
        "no_progress",
        "worker_retry_exhausted",
        "server_shutdown",
        "mcp_unavailable",
    }
)


def safe_code(value):
    return value if isinstance(value, str) and value in CODES else "internal_error"


def safe_tool(value):
    if isinstance(value, str) and value in RETRYABLE_TOOLS:
        return value
    return "unknown_tool"


def safe_id(value):
    try:
        return str(UUID(str(value))) if value else None
    except (TypeError, ValueError):
        return None


def identifiers(step=None):
    request, run = safe_id(REQUEST_ID.get()), safe_id(RUN_ID.get())
    keys = {"request_id": request, "run_id": run}
    if type(step) is int and 1 <= step <= 6:
        keys["step_id"] = f"{run}:{step}" if run else None
    return keys


@contextmanager
def run_scope(record):
    run = RUN_ID.set(record.get("id"))
    token = REQUEST_ID.set(record.get("request_id") or REQUEST_ID.get())
    try:
        yield
    finally:
        REQUEST_ID.reset(token)
        RUN_ID.reset(run)


def elapsed_ms(started):
    return round((perf_counter() - started) * 1000, 3)


def log_operation(operation, duration_ms, *, step=None, attempt=None, tool=None, code=None):
    entry = {
        **identifiers(step),
        "operation": operation,
        "duration_ms": duration_ms,
        "outcome": "error" if code else "ok",
    }
    if attempt is not None:
        entry["attempt"] = attempt
    if tool is not None:
        entry["tool"] = safe_tool(tool)
    if code:
        entry["error_code"] = safe_code(code)
    LOGGER.info("%s", json.dumps(entry, separators=(",", ":")))


@contextmanager
def timed(operation):
    started = perf_counter()
    try:
        yield
    except Exception as error:
        log_operation(operation, elapsed_ms(started), code=classify_failure(error)[0])
        raise
    else:
        log_operation(operation, elapsed_ms(started))


def public_call_id(value):
    if not isinstance(value, str) or not value:
        return None
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]


def configure_trace_logging():
    if not LOGGER.handlers:
        LOGGER.addHandler(logging.StreamHandler())
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
