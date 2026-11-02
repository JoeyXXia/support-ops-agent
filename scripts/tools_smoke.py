import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.tools.executor import ToolExecutor
from packages.tools.schemas import AuthContext

executor = ToolExecutor()
context = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")
for name in ("get_workspace_usage", "get_recent_errors"):
    print(
        json.dumps(
            executor.execute(name, {"workspace_id": "ws-alpha"}, context=context).model_dump(
                mode="json"
            ),
            ensure_ascii=False,
        )
    )
