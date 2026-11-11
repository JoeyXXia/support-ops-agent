"""Trusted local operator CLI for mock read-only recovery and process fault injection."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.agent.persistence import RunRepository, checkpoint_saver, execute_run
from packages.agent.scenario_mock import SupportScenarioMock
from packages.storage.database import engine_from_environment
from packages.tools.schemas import AuthContext


async def run(args):
    engine = engine_from_environment()
    repository = RunRepository(engine)
    context = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")
    try:
        run_id = str(uuid4()) if args.action == "create" else str(UUID(args.run_id))
        if args.action == "create":
            repository.create(
                {
                    "id": run_id,
                    "question": "ws-alpha 429",
                    "mode": "mock",
                    "status": "queued",
                    "reason": "",
                    "answer": "",
                    "citations": [],
                    "events": [],
                    "state_path": [],
                    "model_calls": 0,
                    "tool_calls": 0,
                },
                context,
                knowledge=False,
                product_version=None,
            )
            print(json.dumps({"run_id": run_id}), flush=True)
        if args.action == "show":
            print(json.dumps(repository.load(run_id, context)["record"], ensure_ascii=False))
            return

        class FaultProvider(SupportScenarioMock):
            async def generate(self, request):
                has_tools = any(
                    isinstance(item, dict) and item.get("type") == "function_call_output"
                    for item in request.messages
                )
                if args.pause_at == "before-model" or (
                    args.pause_at == "after-tools" and has_tools
                ):
                    print(json.dumps({"fault_ready": args.pause_at}), flush=True)
                    await asyncio.Event().wait()
                response = await super().generate(request)
                if args.pause_at == "after-model" and not has_tools:
                    print(json.dumps({"fault_ready": args.pause_at}), flush=True)
                    await asyncio.Event().wait()
                return response

        async with checkpoint_saver(engine) as saver:
            record = await execute_run(repository, saver, run_id, context, FaultProvider)
            print(json.dumps(record, ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "resume", "show"])
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--pause-at", choices=["before-model", "after-model", "after-tools"])
    arguments = parser.parse_args()
    if arguments.action != "create" and not arguments.run_id:
        parser.error("resume/show require run_id")
    asyncio.run(run(arguments))
