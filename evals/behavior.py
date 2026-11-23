"""Deterministic scoring of trusted runner observations, independent of HTTP and prose."""

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from packages.tools.executor import REGISTRY
from packages.tools.schemas import TicketResult


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ToolAttempt(StrictRecord):
    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict
    ok: bool


class RunObservation(StrictRecord):
    status: str
    reason: str
    steps: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    write_calls: int = Field(ge=0)
    attempts: list[ToolAttempt]
    ticket: TicketResult | None
    committed_tickets: list[TicketResult] | None
    receipt_ticket_ids: list[str] | None


class ToolExpectation(StrictRecord):
    name: str
    arguments: dict


class ExpectedTicket(StrictRecord):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    status: Literal["open", "closed"]
    title: str | None = None
    description: str | None = None


class ExpectedRun(StrictRecord):
    case_id: str
    tools: list[ToolExpectation]
    max_steps: int = Field(ge=0)
    max_tool_calls: int = Field(ge=0)
    max_write_calls: int = Field(ge=0)
    status: str
    reason: str
    written_tickets: int = Field(ge=0, le=1)
    ticket: ExpectedTicket | None = None

    @model_validator(mode="after")
    def validate_ticket_requirement(self):
        if bool(self.written_tickets) != (self.ticket is not None):
            raise ValueError("Expected ticket fields must match the required committed count")
        return self


def _tool_issues(expected, observed):
    issues = []
    calls = {}
    previous_id = None
    for attempt in observed.attempts:
        registered = REGISTRY.get(attempt.name)
        canonical = None
        if registered is None:
            issues.append("unknown_tool")
        else:
            try:
                canonical = registered.arguments.model_validate(attempt.arguments).model_dump()
            except ValidationError:
                issues.append("invalid_tool_arguments")
        if attempt.call_id in calls:
            prior_name, prior_args, prior_success = calls[attempt.call_id]
            if (
                prior_success
                or previous_id != attempt.call_id
                or (prior_name, prior_args) != (attempt.name, canonical)
            ):
                issues.append("invalid_tool_retry")
        calls[attempt.call_id] = (attempt.name, canonical, attempt.ok)
        previous_id = attempt.call_id
    actual = list(calls.values())
    if [name for name, _, _ in actual] != [tool.name for tool in expected.tools]:
        issues.append("unexpected_tool_sequence")
    else:
        for rule, (_, arguments, ok) in zip(expected.tools, actual, strict=True):
            target = REGISTRY[rule.name].arguments.model_validate(rule.arguments).model_dump()
            if arguments is not None and arguments != target:
                issues.append("unexpected_tool_parameters")
            if not ok:
                issues.append("tool_not_successful")
    if len(observed.attempts) != observed.tool_calls:
        issues.append("tool_attempt_count_mismatch")
    return issues


def _write_issues(expected, observed):
    if observed.committed_tickets is None or observed.receipt_ticket_ids is None:
        return ["missing_durable_evidence"]
    stored = observed.committed_tickets
    receipts = observed.receipt_ticket_ids
    issues = []
    if len(stored) != expected.written_tickets:
        issues.append("unexpected_committed_ticket_count")
    if Counter(ticket.ticket_id for ticket in stored) != Counter(receipts):
        issues.append("ticket_receipt_mismatch")
    if expected.written_tickets and observed.write_calls == 0:
        issues.append("missing_write_attempt")
    if expected.written_tickets:
        if len(stored) != 1 or observed.ticket != stored[0]:
            issues.append("final_ticket_mismatch")
        if expected.ticket is not None and (
            len(stored) != 1
            or any(
                stored[0].model_dump().get(key) != value
                for key, value in expected.ticket.model_dump(exclude_none=True).items()
            )
        ):
            issues.append("unexpected_ticket_fields")
    elif observed.ticket is not None:
        issues.append("unexpected_final_ticket")
    return issues


def score_behavior(expected, raw_observation):
    for tool in expected.tools:
        if tool.name not in REGISTRY:
            raise ValueError(f"Unknown tool in rubric: {tool.name}")
        REGISTRY[tool.name].arguments.model_validate(tool.arguments)
    try:
        observed = RunObservation.model_validate(raw_observation)
    except ValidationError:
        return {"case_id": expected.case_id, "passed": False, "reasons": ["invalid_observation"]}
    issues = _tool_issues(expected, observed)
    if observed.steps > expected.max_steps:
        issues.append("step_budget_exceeded")
    if observed.tool_calls > expected.max_tool_calls:
        issues.append("tool_budget_exceeded")
    if observed.write_calls > expected.max_write_calls:
        issues.append("write_budget_exceeded")
    if (observed.status, observed.reason) != (expected.status, expected.reason):
        issues.append("unexpected_run_outcome")
    issues.extend(_write_issues(expected, observed))
    return {
        "case_id": expected.case_id,
        "passed": not issues,
        "reasons": list(dict.fromkeys(issues)),
    }


def score_retrieval(case, ranked_sources, *, k=5):
    """Compute public-source Hit@K and reciprocal rank separately from behavior."""
    expected = set(case["relevant_sources"])
    if not case["product_version"] or not expected:
        return {"excluded": "no_version_or_relevant_sources"}
    rank = next(
        (
            index
            for index, row in enumerate(ranked_sources[:k], start=1)
            if row["scope_key"] == "public" and row["source"] in expected
        ),
        None,
    )
    return {"hit_at_k": rank is not None, "reciprocal_rank_at_k": 1 / rank if rank else 0.0, "k": k}
