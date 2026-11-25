"""Private conversation state and explicitly confirmed preferences."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from packages.agent.conversation import Version

router = APIRouter(tags=["memory"])


class MemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NewConversation(MemoryInput):
    use_preference: bool = False


class CurrentVersion(MemoryInput):
    product_version: Version | None


class ConfirmedPreference(MemoryInput):
    product_version: Version
    confirmed: bool


def conversation_for(request, conversation_id):
    try:
        return request.app.state.conversations.get(conversation_id, request.state.context)
    except LookupError:
        raise HTTPException(404, "会话不存在或当前身份无权访问。") from None


def preferences_for(request):
    repository = request.app.state.preferences
    if repository is None:
        raise HTTPException(409, "长期偏好需要持久化模式。")
    return repository


@router.post("/conversations", status_code=201)
async def create_conversation(body: NewConversation, request: Request):
    owner = request.state.context
    version = preferences_for(request).get(owner) if body.use_preference else None
    try:
        identifier, record = request.app.state.conversations.create(owner)
    except OverflowError:
        raise HTTPException(503, "会话已满，请稍后重试。") from None
    if version is not None:
        record.set_version(version, source="confirmed_preference")
    return {"id": str(identifier), **record.snapshot()}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: UUID, request: Request):
    return conversation_for(request, conversation_id).snapshot()


@router.patch("/conversations/{conversation_id}")
async def update_conversation(conversation_id: UUID, body: CurrentVersion, request: Request):
    record = conversation_for(request, conversation_id)
    record.set_version(body.product_version)
    return record.snapshot()


@router.get("/preferences")
async def get_preference(request: Request):
    version = preferences_for(request).get(request.state.context)
    return {"product_version": version}


@router.put("/preferences")
async def set_preference(body: ConfirmedPreference, request: Request):
    if not body.confirmed:
        raise HTTPException(422, "保存长期偏好需要显式确认。")
    preferences_for(request).set(request.state.context, body.product_version)
    return {"product_version": body.product_version}


@router.delete("/preferences", status_code=204, response_class=Response)
async def delete_preference(request: Request):
    owner = request.state.context
    preferences_for(request).delete(owner)
    request.app.state.conversations.clear_preference(owner)
    return Response(status_code=204)
