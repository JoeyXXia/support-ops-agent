"""Verify budget enforcement and JSON-safe accounting through the durable API."""

import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from support import PostgreSQLCase

from apps.api.main import app
from apps.api.runs import DemoProvider
from packages.agent.models import ModelResponse, ToolCall, Usage


class CostPersistenceTests(PostgreSQLCase):
    def test_durable_run_stops_and_publishes_accounting(self):
        async def generate(provider, request):
            return ModelResponse(
                text="",
                model="cost-persistence-fixture",
                mode="mock",
                usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
                tool_calls=[
                    ToolCall(
                        call_id="usage",
                        name="get_workspace_usage",
                        arguments={"workspace_id": "ws-alpha"},
                    )
                ],
            )

        async def verify():
            async with self.serving(app), self.client(app) as http:
                response = await http.post("/runs", json={"question": "ws-alpha 429"})
                self.assertEqual(response.status_code, 202)
                path = "/runs/" + response.json()["id"]
                stream = await asyncio.wait_for(http.get(path + "/events"), 10)
                record = (await http.get(path)).json()
                self.assertEqual(
                    (record["status"], record["reason"], record["tool_calls"]),
                    ("stopped", "cost_budget", 0),
                )
                accounting = record["accounting"]
                self.assertEqual(accounting["estimated_total_usd"], "0.02")
                self.assertEqual(accounting["calls"][0]["usage_source"], "mock_fixture")
                final = json.loads(
                    [line[6:] for line in stream.text.splitlines() if line.startswith("data: ")][-1]
                )
                self.assertEqual(final["type"], "run.finished")
                self.assertEqual(final["data"]["run"]["accounting"], accounting)

        with TemporaryDirectory() as directory:
            path = Path(directory) / "prices.json"
            path.write_text(
                json.dumps(
                    {
                        "currency": "USD",
                        "prices": [
                            {
                                "model": "cost-persistence-fixture",
                                "effective_on": "2000-01-01",
                                "input_per_million": "1000",
                                "output_per_million": "2000",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            env = self.api_environment() | {
                "SUPPORTOPS_PRICE_CATALOG": str(path),
                "SUPPORTOPS_RUN_BUDGET_USD": "0.02",
            }
            with patch.dict(os.environ, env), patch.object(DemoProvider, "generate", generate):
                asyncio.run(verify())
