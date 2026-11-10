from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WorkspaceArgs(StrictModel):
    workspace_id: str = Field(min_length=1, max_length=80, pattern=r"^ws-[a-z0-9-]+$")


class RecentErrorsArgs(WorkspaceArgs):
    limit: int = Field(default=5, ge=1, le=20)


class AuthContext(StrictModel):
    """Construct only from trusted server identity, never from model arguments."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    user_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    role: Literal["member", "support"]
    assigned_tenant_ids: tuple[str, ...] = ()


class UsageResult(StrictModel):
    workspace_id: str
    plan: str
    version: str
    minute_requests: int = Field(ge=0)
    minute_limit: int = Field(gt=0)
    monthly_requests: int = Field(ge=0)
    monthly_limit: int = Field(gt=0)
    synthetic: Literal[True] = True


class ErrorEvent(StrictModel):
    id: str
    occurred_at: datetime
    status_code: Literal[401, 403, 429]
    reason: str | None = None
    retry_after_seconds: int | None = Field(default=None, ge=0)


class RecentErrorsResult(StrictModel):
    workspace_id: str
    events: list[ErrorEvent]
    synthetic: Literal[True] = True


class SearchDocsArgs(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    product_version: Literal["1.0", "2.0"]


class DocEvidence(StrictModel):
    chunk_id: str
    source: str
    body: str
    source_path: str
    version_number: int
    product_version: str
    start_line: int
    end_line: int


class SearchDocsResult(StrictModel):
    sources: list[DocEvidence] = Field(max_length=5)


class ToolFailure(StrictModel):
    code: Literal["unknown_tool", "invalid_arguments", "forbidden", "not_found", "internal_error"]
    message: str
    retryable: bool = False


class ToolResult(StrictModel):
    tool: str
    ok: bool
    data: UsageResult | RecentErrorsResult | SearchDocsResult | None = None
    error: ToolFailure | None = None
