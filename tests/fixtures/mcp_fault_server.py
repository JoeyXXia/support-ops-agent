"""Test-only subprocess fault injection, never selectable by application configuration."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import asyncio

from apps.mcp.server import create_server, serve
from packages.tools.executor import ToolExecutor

executor = ToolExecutor()
original = executor.execute


def injected(name, arguments, *, context):
    if name == "get_workspace_usage":
        Path(os.environ["MCP_TEST_MARKER"]).write_text("call-started", encoding="utf-8")
        if os.environ["MCP_TEST_FAULT"] == "drop":
            os._exit(7)
        time.sleep(60)
    return original(name, arguments, context=context)


executor.execute = injected
asyncio.run(serve(create_server(os.environ["SUPPORTOPS_MCP_SUBJECT"], executor=executor)))
