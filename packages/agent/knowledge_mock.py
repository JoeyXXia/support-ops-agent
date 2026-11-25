"""Fixed synthetic knowledge demo with conservative abstention."""

import json
import re

from .models import ModelResponse, ToolCall

QUESTIONS = {
    "starter配额是多少？": "quotas#limits",
    "升级到2.0会重置额度吗？": "versions#versions",
    "401能用429的办法处理吗？": "auth#errors",
    "reason=minute_rate_limit且Retry-After=20，怎么办？": "rate-limit#diagnosis",
    "reason=monthly_quota_exhausted，持续重试有用吗？": "monthly-quota#diagnosis",
}


PLAN = re.compile(r"\b(?:starter|team)\b|入门套餐|协作套餐")
QUOTA = re.compile(r"配额|限额|调用上限|多少[次条个]?(?:请求|调用)|请求几次")
COMMERCIAL = re.compile(r"价格|折扣|报价|合同|费用|付费|收费|多少钱")


def target_source(question):
    explicit = QUESTIONS.get(question)
    if explicit:
        return explicit
    question = question.casefold()
    if PLAN.search(question) and QUOTA.search(question) and not COMMERCIAL.search(question):
        return "quotas#limits"
    return None


class KnowledgeScenarioMock:
    def __init__(self, question, product_version):
        self.question, self.product_version = question, product_version

    async def generate(self, request):
        outputs = [
            json.loads(item["output"])
            for item in request.messages
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ]
        if not self.product_version:
            status, ids = "clarify", []
        elif not outputs:
            return ModelResponse(
                text="",
                mode="mock",
                model="knowledge-mock-v1",
                tool_calls=[
                    ToolCall(
                        call_id="docs-1",
                        name="search_docs",
                        arguments={
                            "question": self.question,
                            "product_version": self.product_version,
                        },
                    )
                ],
            )
        else:
            target = target_source(self.question)
            matching = [row for row in outputs[-1]["data"]["sources"] if row["source"] == target]
            if len({row["body"] for row in matching}) > 1:
                status, ids = "conflict", []
            elif matching:
                status, ids = "answered", [matching[0]["chunk_id"]]
            else:
                status, ids = "insufficient", []
        return ModelResponse(
            text=json.dumps({"status": status, "citation_ids": ids}),
            mode="mock",
            model="knowledge-mock-v1",
        )
