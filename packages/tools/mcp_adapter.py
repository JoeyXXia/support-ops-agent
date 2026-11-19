"""Controlled local MCP workspace reads; no arbitrary server or identity from model data."""

import asyncio
import json
import logging
import math

import anyio
from mcp import Client, MCPError
from mcp.types import CONNECTION_CLOSED, REQUEST_TIMEOUT
from pydantic import ValidationError

from apps.mcp.client import server_parameters
from apps.mcp.server import READ_TOOLS, REPLIES, subject_context
from packages.tools.executor import REGISTRY, ToolExecutor
from packages.tools.schemas import AuthContext, ToolFailure, ToolResult

logger = logging.getLogger(__name__)


def failure(name, code, *, retryable=False):
    messages = {
        "invalid_arguments": "工具参数未通过验证。",
        "forbidden": "身份或访问未获授权。",
        "timeout": "MCP 只读调用超时。",
        "unavailable": "MCP 只读连接暂不可用。",
        "internal_error": "MCP 只读结果或服务契约未通过验证。",
    }
    return ToolResult(
        tool=name,
        ok=False,
        error=ToolFailure(code=code, message=messages[code], retryable=retryable),
    )


def leaves(error):
    if isinstance(error, BaseExceptionGroup):
        return [item for nested in error.exceptions for item in leaves(nested)]
    return [error]


def transport_failure(name, error):
    errors = leaves(error)
    if any(isinstance(e, asyncio.CancelledError) for e in errors):
        raise asyncio.CancelledError from None
    if any(
        isinstance(e, TimeoutError) or isinstance(e, MCPError) and e.error.code == REQUEST_TIMEOUT
        for e in errors
    ):
        return failure(name, "timeout", retryable=True)
    if all(
        isinstance(
            e,
            (
                OSError,
                EOFError,
                anyio.EndOfStream,
                anyio.BrokenResourceError,
                anyio.ClosedResourceError,
            ),
        )
        or isinstance(e, MCPError)
        and e.error.code == CONNECTION_CLOSED
        for e in errors
    ):
        return failure(name, "unavailable", retryable=True)
    return failure(name, "internal_error")


class MCPReadAdapter:
    def __init__(self, *, executor=None, timeout=5.0):
        if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 10:
            raise ValueError("MCP timeout must be finite and between 0 and 10 seconds")
        self.executor = executor or ToolExecutor()
        self.timeout = timeout

    async def execute(self, name, arguments, *, context):
        if name not in READ_TOOLS:
            return failure(name, "forbidden")
        try:
            if not isinstance(context, AuthContext) or context != subject_context(
                context.user_id, self.executor
            ):
                return failure(name, "forbidden")
        except ValueError:
            return failure(name, "forbidden")
        try:
            REGISTRY[name].arguments.model_validate(arguments)
        except ValidationError:
            return failure(name, "invalid_arguments")
        try:
            async with asyncio.timeout(self.timeout):
                async with Client(
                    server_parameters(context.user_id), read_timeout_seconds=self.timeout
                ) as client:
                    if (
                        client.server_info is None
                        or client.server_info.name != "supportops-read-tools"
                    ):
                        return failure(name, "internal_error")
                    tools = (await client.list_tools()).tools
                    if {t.name for t in tools} != set(READ_TOOLS) or len(tools) != len(READ_TOOLS):
                        return failure(name, "internal_error")
                    for tool in tools:
                        if (
                            tool.input_schema != REGISTRY[tool.name].arguments.model_json_schema()
                            or tool.output_schema != REPLIES[tool.name].model_json_schema()
                        ):
                            return failure(name, "internal_error")
                    reply = await client.call_tool(name, arguments)
                    if not isinstance(reply.structured_content, dict):
                        return failure(name, "internal_error")
                    # Validate protocol dates in JSON mode; keep strict input rules.
                    result = REPLIES[name].model_validate_json(json.dumps(reply.structured_content))
                    if reply.is_error != (not result.ok):
                        return failure(name, "internal_error")
                    if result.ok:
                        if (
                            result.error is not None
                            or result.data is None
                            or result.data.workspace_id != arguments["workspace_id"]
                        ):
                            return failure(name, "internal_error")
                    elif result.error is None or result.data is not None:
                        return failure(name, "internal_error")
                    return ToolResult.model_validate(result.model_dump())
        except asyncio.CancelledError:
            raise
        except BaseExceptionGroup as error:
            if not isinstance(error, Exception):
                if any(isinstance(e, asyncio.CancelledError) for e in leaves(error)):
                    raise asyncio.CancelledError from None
                raise
            return transport_failure(name, error)
        except Exception as error:
            logger.warning("Controlled MCP read failed")
            return transport_failure(name, error)


class MCPToolExecutor(ToolExecutor):
    read_tool_transport = "mcp"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mcp = MCPReadAdapter(executor=self)

    def execute(self, name, arguments, *, context):
        if name in READ_TOOLS:
            raise RuntimeError("MCP workspace reads require execute_async")
        return super().execute(name, arguments, context=context)

    async def execute_async(self, name, arguments, *, context):
        if name in READ_TOOLS:
            return await self.mcp.execute(name, arguments, context=context)
        return await super().execute_async(name, arguments, context=context)
