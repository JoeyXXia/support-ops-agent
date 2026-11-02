import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.agent.loop import AgentLoop
from packages.agent.scenario_mock import SupportScenarioMock
from packages.tools.executor import ToolExecutor
from packages.tools.schemas import AuthContext


async def main():
    result = await AgentLoop(SupportScenarioMock(), ToolExecutor()).run(
        "CloudDesk ws-alpha升级后一直429，帮我排查。",
        context=AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member"),
    )
    print(result.model_dump_json(indent=2))
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
