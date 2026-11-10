import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from packages.agent.models import ToolDefinition

from .schemas import (
    AuthContext,
    ErrorEvent,
    RecentErrorsArgs,
    RecentErrorsResult,
    SearchDocsArgs,
    StrictModel,
    ToolFailure,
    ToolResult,
    UsageResult,
    WorkspaceArgs,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolSpec:
    description: str
    arguments: type[StrictModel]
    handler: str


REGISTRY = {
    "search_docs": ToolSpec("按产品版本检索授权文档，返回出处和原文", SearchDocsArgs, "_search"),
    "get_workspace_usage": ToolSpec(
        "读取获授权工作区的合成用量与套餐限额", WorkspaceArgs, "_usage"
    ),
    "get_recent_errors": ToolSpec(
        "读取获授权工作区最近的合成错误，按时间倒序", RecentErrorsArgs, "_errors"
    ),
}


class ToolExecutor:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or Path(__file__).resolve().parents[2] / "data/demo"
        self.tenants = json.loads((self.data_dir / "tenants.json").read_text(encoding="utf-8"))
        self.facts = json.loads((self.data_dir / "product_facts.json").read_text(encoding="utf-8"))

    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=name,
                description=spec.description,
                parameters=spec.arguments.model_json_schema(),
            )
            for name, spec in REGISTRY.items()
        ]

    def execute(self, name: str, arguments: Any, *, context: AuthContext) -> ToolResult:
        def fail(code, message):
            return ToolResult(tool=name, ok=False, error=ToolFailure(code=code, message=message))

        spec = REGISTRY.get(name)
        if spec is None:
            return fail("unknown_tool", "工具不存在。")
        try:
            args = spec.arguments.model_validate(arguments)
        except ValidationError:
            return fail("invalid_arguments", "工具参数未通过验证。")
        # Authenticate the server-supplied principal against the demo membership fixture.
        if not isinstance(context, AuthContext):
            return fail("forbidden", "身份或工作区访问未获授权。")
        home = next((t for t in self.tenants if t["id"] == context.tenant_id), None)
        user = (
            next((u for u in home["users"] if u["id"] == context.user_id), None) if home else None
        )
        if user is None or user["role"] != context.role:
            return fail("forbidden", "身份或工作区访问未获授权。")
        allowed = (
            {context.tenant_id} if context.role == "member" else set(context.assigned_tenant_ids)
        )
        if name == "search_docs":
            from .knowledge import search_docs

            try:
                scope = context.tenant_id if context.tenant_id in allowed else None
                return ToolResult(tool=name, ok=True, data=search_docs(args, scope))
            except ValueError:
                logger.error("tool.failed tool=%s code=internal_error", name)
                return fail("internal_error", "文档检索暂不可用。")
        # Search only authorized tenants, avoiding a global existence oracle.
        workspace = next(
            (
                w
                for t in self.tenants
                if t["id"] in allowed
                for w in t["workspaces"]
                if w["id"] == args.workspace_id
            ),
            None,
        )
        if workspace is None:
            return fail("not_found", "工作区不存在或不可访问。")
        try:
            result = getattr(self, spec.handler)(args, workspace)
            return ToolResult(tool=name, ok=True, data=result)
        except (OSError, ValueError, KeyError, TypeError):
            logger.error("tool.failed tool=%s code=internal_error", name)
            return fail("internal_error", "工具数据暂不可用。")

    def _usage(self, args: WorkspaceArgs, workspace: dict) -> UsageResult:
        plan = self.facts["plans"][workspace["plan"]]
        return UsageResult(
            workspace_id=args.workspace_id,
            plan=workspace["plan"],
            version=workspace["version"],
            minute_requests=workspace["minute_requests"],
            minute_limit=plan["requests_per_minute"],
            monthly_requests=workspace["monthly_requests"],
            monthly_limit=plan["monthly_requests"],
        )

    def _errors(self, args: RecentErrorsArgs, workspace: dict) -> RecentErrorsResult:
        # Private events are not loaded until workspace authorization has passed.
        rows = json.loads((self.data_dir / "recent_errors.json").read_text(encoding="utf-8"))
        events = [
            ErrorEvent.model_validate_json(json.dumps(row["event"]))
            for row in rows
            if row["workspace_id"] == args.workspace_id
        ]
        for event in events:
            if event.occurred_at.utcoffset() is None:
                raise ValueError("timezone required")
        events.sort(key=lambda event: (event.occurred_at, event.id), reverse=True)
        return RecentErrorsResult(workspace_id=args.workspace_id, events=events[: args.limit])
