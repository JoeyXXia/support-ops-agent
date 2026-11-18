"""Independent local stdio MCP endpoint; not the web API or the Agent adapter."""

import asyncio
import json
import logging
import os
import sys
from typing import Literal

from mcp import MCPError
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    INVALID_PARAMS,
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)
from packages.tools.executor import REGISTRY, ToolExecutor
from packages.tools.schemas import (
    AuthContext,
    RecentErrorsResult,
    ToolFailure,
    ToolResult,
    UsageResult,
)

READ_TOOLS = ("get_workspace_usage", "get_recent_errors")
SUBJECT_ENV = "SUPPORTOPS_MCP_SUBJECT"
logger = logging.getLogger(__name__)


class UsageReply(ToolResult):
    tool: Literal["get_workspace_usage"]
    data: UsageResult | None = None


class ErrorsReply(ToolResult):
    tool: Literal["get_recent_errors"]
    data: RecentErrorsResult | None = None


REPLIES = {"get_workspace_usage": UsageReply, "get_recent_errors": ErrorsReply}


def subject_context(subject: str | None, executor: ToolExecutor) -> AuthContext:
    """A trusted local launcher selects a seed account, never tool arguments or _meta."""
    for tenant in executor.tenants:
        for user in tenant["users"]:
            if subject and user["id"] == subject:
                return AuthContext(
                    user_id=user["id"],
                    tenant_id=tenant["id"],
                    role=user["role"],
                    assigned_tenant_ids=(tenant["id"],) if user["role"] == "support" else (),
                )
    raise ValueError("A known seed subject must be configured by the trusted local launcher")


def create_server(subject: str | None, *, executor: ToolExecutor | None = None) -> Server:
    executor = executor or ToolExecutor()
    context = subject_context(subject, executor)

    async def list_tools(
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        return ListToolsResult(
            tools=[
                Tool(
                    name=name,
                    description=REGISTRY[name].description,
                    input_schema=REGISTRY[name].arguments.model_json_schema(),
                    output_schema=REPLIES[name].model_json_schema(),
                    annotations=ToolAnnotations(
                        read_only_hint=True,
                        destructive_hint=False,
                        idempotent_hint=True,
                        open_world_hint=False,
                    ),
                )
                for name in READ_TOOLS
            ]
        )

    async def call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
        if params.name not in READ_TOOLS:
            # A manually crafted call cannot reach any write or non-advertised tool.
            raise MCPError(INVALID_PARAMS, "Tool is not exposed by this read-only server")
        try:
            result = await asyncio.to_thread(
                executor.execute,
                params.name,
                params.arguments or {},
                context=context,
            )
            reply = REPLIES[params.name].model_validate(result.model_dump())
        except Exception:
            logger.error("Read-only MCP tool execution failed")
            reply = REPLIES[params.name](
                tool=params.name,
                ok=False,
                error=ToolFailure(code="internal_error", message="只读工具暂不可用。"),
            )
        data = reply.model_dump(mode="json")
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False))],
            structured_content=data,
            is_error=not reply.ok,
        )

    return Server(
        "supportops-read-tools",
        version="0.1.0",
        instructions=(
            "Synthetic local read-only CloudDesk tools. Subject is fixed by the trusted launcher."
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def serve(server: Server):
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    try:
        server = create_server(os.getenv(SUBJECT_ENV))
    except ValueError:
        print("Configure SUPPORTOPS_MCP_SUBJECT with a known local seed user.", file=sys.stderr)
        return 2
    asyncio.run(serve(server))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
