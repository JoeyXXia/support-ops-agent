"""Small real SDK client for the fixed local read-only MCP server, not an Agent adapter."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import Client, MCPError, StdioServerParameters
from mcp.types import INVALID_PARAMS
from packages.tools.executor import REGISTRY, ToolExecutor

from .server import READ_TOOLS, SUBJECT_ENV, subject_context

ROOT = Path(__file__).resolve().parents[2]


def server_parameters(subject: str) -> StdioServerParameters:
    # Do not accept arbitrary commands, shell fragments, URLs, or inherited app secrets.
    subject_context(subject, ToolExecutor())
    return StdioServerParameters(
        command=sys.executable,
        args=["-u", "-m", "apps.mcp.server"],
        cwd=str(ROOT),
        encoding="utf-8",
        env={SUBJECT_ENV: subject, "PYTHONUTF8": "1"},
    )


async def smoke(subject: str) -> dict:
    executor = ToolExecutor()
    context = subject_context(subject, executor)
    own = next(t["workspaces"][0]["id"] for t in executor.tenants if t["id"] == context.tenant_id)
    foreign = next(
        t["workspaces"][0]["id"] for t in executor.tenants if t["id"] != context.tenant_id
    )
    observations = []
    async with asyncio.timeout(25):
        async with Client(server_parameters(subject), read_timeout_seconds=5) as client:
            discovery = await client.list_tools()
            assert [tool.name for tool in discovery.tools] == list(READ_TOOLS)
            for tool in discovery.tools:
                assert tool.input_schema == REGISTRY[tool.name].arguments.model_json_schema()
                assert tool.annotations.read_only_hint and not tool.annotations.destructive_hint
                assert tool.output_schema is not None
            for name in READ_TOOLS:
                args = {"workspace_id": own}
                reply = await client.call_tool(name, args)
                expected = executor.execute(name, args, context=context).model_dump(mode="json")
                assert not reply.is_error and reply.structured_content == expected
                assert json.loads(reply.content[0].text) == reply.structured_content
                observations.append(
                    {"case": name, "is_error": reply.is_error, "result": reply.structured_content}
                )
            for label, name, args, code in (
                ("cross_tenant", READ_TOOLS[0], {"workspace_id": foreign}, "not_found"),
                (
                    "strict_boolean_limit",
                    READ_TOOLS[1],
                    {"workspace_id": own, "limit": True},
                    "invalid_arguments",
                ),
                (
                    "forged_subject",
                    READ_TOOLS[0],
                    {"workspace_id": own, "user_id": "user-beta"},
                    "invalid_arguments",
                ),
            ):
                reply = await client.call_tool(name, args)
                assert reply.is_error and reply.structured_content["error"]["code"] == code
                observations.append({"case": label, "is_error": True, "code": code})
            reply = await client.call_tool(
                READ_TOOLS[0],
                {"workspace_id": foreign},
                meta={"user_id": "user-beta", "tenant_id": "tenant-beta", "role": "support"},
            )
            assert reply.is_error and reply.structured_content["error"]["code"] == "not_found"
            observations.append({"case": "forged_meta", "is_error": True, "code": "not_found"})
            try:
                await client.call_tool("propose_ticket", {"workspace_id": own})
            except MCPError as error:
                assert error.error.code == INVALID_PARAMS
                observations.append({"case": "unexposed_write", "protocol_error": error.error.code})
            else:
                raise AssertionError("Write tool unexpectedly exposed")
            result = {
                "subject": subject,
                "transport": "real child process stdio",
                "sdk": "mcp==2.3.0",
                "protocol_version": client.protocol_version,
                "server": client.server_info.model_dump(mode="json"),
                "tools": [tool.model_dump(mode="json", by_alias=True) for tool in discovery.tools],
                "observations": observations,
            }
    result["client_context_closed"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subject",
        required=True,
        choices=[u["id"] for t in ToolExecutor().tenants for u in t["users"]],
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(smoke(args.subject)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
