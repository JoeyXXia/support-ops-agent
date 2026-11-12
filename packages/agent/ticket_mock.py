"""Deterministic proposal demo. The only action is saving a pending draft."""

from .models import ModelResponse, ToolCall


class TicketDraftMock:
    def __init__(self, question):
        self.question = question

    async def generate(self, request):
        has_output = any(
            isinstance(item, dict) and item.get("type") == "function_call_output"
            for item in request.messages
        )
        if has_output:
            return ModelResponse(
                text="[mock] 已生成待审核工单草稿，请核对目标租户、工作区与内容。",
                mode="mock",
                model="ticket-draft-mock-v1",
            )
        return ModelResponse(
            text="",
            mode="mock",
            model="ticket-draft-mock-v1",
            tool_calls=[
                ToolCall(
                    call_id="draft-1",
                    name="propose_ticket",
                    arguments={
                        "workspace_id": "ws-alpha",
                        "title": "CloudDesk 429 排障转人工",
                        "description": self.question,
                    },
                )
            ],
        )
