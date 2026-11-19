"""Server-side transport selection. Request bodies and model arguments cannot select it."""

import os

from .executor import ToolExecutor

TRANSPORT_ENV = "SUPPORTOPS_READ_TOOL_TRANSPORT"


def build_executor(*, transport=None, **kwargs):
    selected = os.getenv(TRANSPORT_ENV, "direct") if transport is None else transport
    if selected == "direct":
        return ToolExecutor(**kwargs)
    if selected == "mcp":
        from .mcp_adapter import MCPToolExecutor

        return MCPToolExecutor(**kwargs)
    raise ValueError("SUPPORTOPS_READ_TOOL_TRANSPORT must be direct or mcp")
