from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class ToolDefinition(BaseModel):
    name: str = Field(min_length=1)
    description: str
    parameters: dict[str, Any]


class ModelRequest(BaseModel):
    messages: list[Message] = Field(min_length=1)
    tools: list[ToolDefinition] = Field(default_factory=list)
    max_output_tokens: int = Field(default=256, ge=16, le=4096)


class ToolCall(BaseModel):
    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]


class Usage(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class ModelResponse(BaseModel):
    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage | None = None
    request_id: str | None = None
    model: str
    mode: Literal["mock", "live"]
    model_config = ConfigDict(extra="forbid")


class ProviderError(Exception):
    def __init__(self, code: str, *, retryable: bool = False, request_id: str | None = None):
        self.code = code
        self.retryable = retryable
        self.request_id = request_id
        super().__init__(code)


class ModelProvider(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...
