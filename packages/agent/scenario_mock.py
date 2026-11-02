"""A scripted mock for one known 429 fixture, not a real reasoning model."""

import json

from .models import ModelResponse, ToolCall


class SupportScenarioMock:
    async def generate(self, request):
        outputs = {
            item["call_id"]: json.loads(item["output"])
            for item in request.messages
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        }
        if not outputs:
            return ModelResponse(
                text="",
                mode="mock",
                model="scenario-mock-v1",
                tool_calls=[
                    ToolCall(
                        call_id="usage-1",
                        name="get_workspace_usage",
                        arguments={"workspace_id": "ws-alpha"},
                    ),
                    ToolCall(
                        call_id="errors-1",
                        name="get_recent_errors",
                        arguments={"workspace_id": "ws-alpha", "limit": 2},
                    ),
                ],
            )
        usage, errors = outputs["usage-1"]["data"], outputs["errors-1"]["data"]
        if (
            usage["minute_requests"] >= usage["minute_limit"]
            and errors["events"][0]["reason"] == "minute_rate_limit"
        ):
            text = (
                f"[mock] ws-alpha分钟用量{usage['minute_requests']}/{usage['minute_limit']}，"
                f"月用量{usage['monthly_requests']}/{usage['monthly_limit']}；"
                "日志表明分钟限流，按Retry-After等待"
                f"{errors['events'][0]['retry_after_seconds']}秒并降低请求频率。"
            )
        else:
            text = "[mock] 当前样例不足以支持预设结论，需要继续核对。"
        return ModelResponse(text=text, mode="mock", model="scenario-mock-v1")
