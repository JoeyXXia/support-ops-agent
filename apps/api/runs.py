"""Single-process local demo run lifecycle. No persistence or visitor isolation."""

import asyncio
from collections import OrderedDict
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from packages.agent.graph import GraphAgent
from packages.agent.knowledge_mock import KnowledgeScenarioMock
from packages.agent.scenario_mock import SupportScenarioMock
from packages.tools.executor import ToolExecutor
from packages.tools.schemas import AuthContext


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=2000)
    knowledge: bool = False
    product_version: Literal["1.0", "2.0"] | None = None


class RunStore:
    def __init__(self, *, max_active=4, max_records=100, provider_factory=None):
        self.records = OrderedDict()
        self.tasks = set()
        self.max_active, self.max_records = max_active, max_records
        self.provider_factory = provider_factory or DemoProvider
        self.executor = ToolExecutor()

    def create(self, question, *, knowledge=False, product_version=None):
        if len(self.tasks) >= self.max_active:
            raise OverflowError("busy")
        while len(self.records) >= self.max_records:
            old = next(
                (
                    key
                    for key, value in self.records.items()
                    if value["status"] not in {"queued", "running"}
                ),
                None,
            )
            if old is None:
                raise OverflowError("full")
            del self.records[old]
        run_id = str(uuid4())
        record = {
            "id": run_id,
            "question": question,
            "mode": "mock",
            "status": "queued",
            "reason": "",
            "answer": "",
            "citations": [],
            "events": [],
            "state_path": [],
            "model_calls": 0,
            "tool_calls": 0,
        }
        self.records[run_id] = record
        task = asyncio.create_task(
            self._execute(record, knowledge=knowledge, product_version=product_version)
        )
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return record.copy()

    async def _execute(self, record, *, knowledge, product_version):
        record["status"] = "running"
        try:
            provider = (
                KnowledgeScenarioMock(record["question"], product_version)
                if knowledge
                else self.provider_factory()
            )
            graph = GraphAgent(provider, self.executor)
            result = await graph.run(
                record["question"],
                context=AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member"),
                grounded=knowledge,
                on_event=lambda event: record["events"].append(event),
            )
            record.update(result.model_dump())
        except asyncio.CancelledError:
            record.update(status="failed", reason="server_shutdown")
            raise
        except Exception:
            record.update(status="failed", reason="internal_error")

    def get(self, run_id: UUID):
        return self.records.get(str(run_id))

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for record in self.records.values():
            if record["status"] in {"queued", "running"}:
                record.update(status="failed", reason="server_shutdown")


class DemoProvider:
    """Only the displayed ws-alpha/429 scenario is scripted; other inputs clarify."""

    async def generate(self, request):
        from packages.agent.models import ModelResponse

        question = next(
            m.content for m in request.messages if hasattr(m, "role") and m.role == "user"
        )
        # A short fixed delay makes polling visible in the local demonstration.
        await asyncio.sleep(0.3)
        if "ws-alpha" not in question or "429" not in question or "ws-beta" in question:
            return ModelResponse(
                text="[mock] 当前仅支持ws-alpha的429演示，请提供该工作区与错误码。",
                model="demo-mock-v1",
                mode="mock",
            )
        return await SupportScenarioMock().generate(request)
