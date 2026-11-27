import os
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from packages.agent.conversation import ConversationStore
from packages.agent.run_queue import QueueFull

from .memory import conversation_for
from .memory import router as memory_router
from .runs import RunInput, RunStore
from .sessions import COOKIE, Sessions


@asynccontextmanager
async def lifespan(app):
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env.local", override=False)
    app.state.sessions = Sessions()
    app.state.subject_stores = {}
    app.state.conversations = ConversationStore()
    app.state.preferences = None
    async with AsyncExitStack() as stack:
        if os.getenv("SUPPORTOPS_PERSIST_RUNS") == "1":
            from packages.storage.database import engine_from_environment
            from packages.storage.preferences import PreferenceRepository

            from .persistent_runs import PersistentRunStore

            engine = engine_from_environment()
            stack.callback(engine.dispose)
            app.state.runs = PersistentRunStore(engine)
            app.state.preferences = PreferenceRepository(engine)
        else:
            app.state.runs = RunStore()
        try:
            yield
        finally:
            await app.state.runs.close()
            for store in app.state.subject_stores.values():
                await store.close()


app = FastAPI(title="SupportOps Agent", version="0.1.0", lifespan=lifespan)
app.include_router(memory_router)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["supportops-api"] = "supportops-api"


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    return HealthResponse()


@app.post("/runs", status_code=202)
async def create_run(body: RunInput, request: Request):
    if body.ticket_draft and body.knowledge:
        raise HTTPException(status_code=422, detail="请选择一种演示模式。")
    conversation = None
    if body.conversation_id is not None:
        if not body.knowledge or body.product_version is not None:
            raise HTTPException(422, "仅知识问答可引用会话；请通过会话接口更正版本。")
        conversation = conversation_for(request, body.conversation_id)
    version = conversation.version if conversation is not None else body.product_version
    try:
        record = request.state.runs.create(
            body.question,
            knowledge=body.knowledge,
            product_version=version,
            ticket_draft=body.ticket_draft,
        )
    except ValueError:
        raise HTTPException(status_code=409, detail="工单草稿需要启用持久化运行模式。") from None
    except QueueFull as error:
        raise capacity_error(error) from None
    except OverflowError:
        raise HTTPException(status_code=503, detail="演示运行已满，请稍后重试。") from None
    if conversation is not None:
        conversation.append(body.question, record["id"])
    return record


@app.get("/runs/{run_id}")
async def get_run(run_id: UUID, request: Request):
    record = request.state.runs.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="运行不存在或当前身份无权访问。")
    return record


@app.post("/runs/{run_id}/resume", status_code=202)
async def resume_run(run_id: UUID, request: Request):
    from .persistent_runs import PersistentRunStore

    store = request.state.runs
    if not isinstance(store, PersistentRunStore):
        raise HTTPException(status_code=409, detail="请启用持久化运行模式。")
    try:
        return store.resume(run_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="运行不存在或当前身份无权访问。") from None
    except QueueFull as error:
        raise capacity_error(error) from None
    except OverflowError:
        raise HTTPException(status_code=409, detail="运行正在执行或已达到并发上限。") from None


def capacity_error(error):
    if error.owner_limit:
        return HTTPException(429, "当前账号进行中的运行已达上限，请等待或取消后重试。")
    return HTTPException(503, "运行队列已满，请稍后重试。")


@app.post("/runs/{run_id}/cancel", status_code=202)
async def cancel_run(run_id: UUID, request: Request):
    from .persistent_runs import PersistentRunStore

    store = request.state.runs
    if not isinstance(store, PersistentRunStore):
        raise HTTPException(409, "取消需要持久化运行模式。")
    try:
        return store.cancel(run_id)
    except LookupError:
        raise HTTPException(404, "运行不存在或当前身份无权访问。") from None


class ExecuteTicketInput(BaseModel):
    model_config = {"extra": "forbid"}
    approval_id: UUID
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)
    payload_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@app.post("/tickets", status_code=201)
async def create_ticket(body: ExecuteTicketInput, request: Request):
    from packages.storage.tickets import ApprovalConflict, TicketRepository

    from .persistent_runs import PersistentRunStore

    store = request.state.runs
    if not isinstance(store, PersistentRunStore):
        raise HTTPException(status_code=409, detail="请启用持久化运行模式。")
    try:
        return TicketRepository(store.repository.engine).create_ticket(
            body.approval_id,
            store.context,
            idempotency_key=body.idempotency_key,
            digest=body.payload_digest,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="审批不存在或不可访问。") from None
    except (PermissionError, ApprovalConflict):
        raise HTTPException(status_code=409, detail="审批或动作 key、参数校验未通过。") from None


class ApprovalInput(BaseModel):
    model_config = {"extra": "forbid"}
    run_id: UUID
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


async def decide_approval(approval_id, body, request, *, approve):
    from packages.storage.tickets import ApprovalConflict, TicketRepository

    from .persistent_runs import PersistentRunStore

    store = request.state.runs
    if not isinstance(store, PersistentRunStore):
        raise HTTPException(status_code=409, detail="请启用持久化运行模式。")
    try:
        with store.repository.engine.begin() as connection:
            TicketRepository(store.repository.engine).decide(
                approval_id,
                store.context,
                run_id=body.run_id,
                digest=body.payload_digest,
                approve=approve,
                connection=connection,
            )
            return store.resume_decision(body.run_id, connection)
    except LookupError:
        raise HTTPException(status_code=404, detail="审批不存在或不可访问。") from None
    except QueueFull as error:
        raise capacity_error(error) from None
    except (ApprovalConflict, OverflowError):
        raise HTTPException(
            status_code=409, detail="审批已失效、已决定或运行正忙，请重新查询。"
        ) from None


@app.post("/approvals/{approval_id}/approve", status_code=202)
async def approve(approval_id: UUID, body: ApprovalInput, request: Request):
    return await decide_approval(approval_id, body, request, approve=True)


@app.post("/approvals/{approval_id}/reject", status_code=202)
async def reject(approval_id: UUID, body: ApprovalInput, request: Request):
    return await decide_approval(approval_id, body, request, approve=False)


@app.middleware("http")
async def authenticate(request: Request, call_next):
    public = request.url.path == "/health" or (
        request.url.path == "/session" and request.method == "POST"
    )
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or parsed.netloc != request.headers.get(
                "host"
            ):
                return JSONResponse(
                    {"detail": "请求来源未获授权。"},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )
    if not public:
        try:
            context = request.app.state.sessions.resolve(request.cookies.get(COOKIE))
        except HTTPException as error:
            return JSONResponse(
                {"detail": error.detail},
                status_code=error.status_code,
                headers={"Cache-Control": "no-store"},
            )
        request.state.context = context
        base = request.app.state.runs
        if context == base.context:
            request.state.runs = base
        else:
            key = context.user_id
            stores = request.app.state.subject_stores
            if key not in stores:
                from .persistent_runs import PersistentRunStore

                stores[key] = (
                    PersistentRunStore(base.repository.engine, context=context)
                    if isinstance(base, PersistentRunStore)
                    else RunStore(context=context)
                )
            request.state.runs = stores[key]
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


class LoginInput(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


@app.post("/session")
async def login(body: LoginInput, request: Request, response: Response):
    sessions = request.app.state.sessions
    token = sessions.login(body.username, body.password)
    sessions.revoke(request.cookies.get(COOKIE))
    response.set_cookie(
        COOKIE,
        token,
        max_age=3600,
        httponly=True,
        samesite="strict",
        secure=os.getenv("SUPPORTOPS_COOKIE_SECURE") == "1",
        path="/",
    )
    return sessions.resolve(token).model_dump(mode="json")


@app.get("/session")
async def session(request: Request):
    return request.state.context.model_dump(mode="json")


@app.delete("/session")
async def logout(request: Request, response: Response):
    request.app.state.sessions.revoke(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"logged_out": True}


@app.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: UUID, request: Request):
    from packages.storage.tickets import TicketRepository

    from .persistent_runs import PersistentRunStore

    store = request.state.runs
    if not isinstance(store, PersistentRunStore):
        raise HTTPException(409, "请启用持久化运行模式。")
    context = request.state.context
    allowed = (context.tenant_id,) if context.role == "member" else context.assigned_tenant_ids
    try:
        return TicketRepository(store.repository.engine).get_ticket(ticket_id, allowed)
    except LookupError:
        raise HTTPException(404, "工单不存在或不可访问。") from None


@app.get("/approvals/{approval_id}")
async def get_approval(approval_id: UUID, request: Request):
    from packages.storage.tickets import TicketRepository

    from .persistent_runs import PersistentRunStore

    store = request.state.runs
    if not isinstance(store, PersistentRunStore):
        raise HTTPException(409, "请启用持久化运行模式。")
    try:
        return TicketRepository(store.repository.engine).get_approval(approval_id, store.context)
    except LookupError:
        raise HTTPException(404, "审批不存在或不可访问。") from None


@app.get("/runs/{run_id}/events")
async def run_events(run_id: UUID, request: Request):
    from fastapi.responses import StreamingResponse
    from starlette.background import BackgroundTask

    from .events import stream_run

    store = request.state.runs
    if store.get(run_id) is None:
        raise HTTPException(404, "运行不存在或当前身份无权访问。")
    if request.headers.get("last-event-id"):
        raise HTTPException(409, "本日不支持游标恢复，请显式重新查询运行。")
    if store.active_streams >= 4:
        raise HTTPException(429, "当前账号实时连接已满。")
    store.active_streams += 1
    released = False

    async def release():
        nonlocal released
        if not released:
            store.active_streams -= 1
            released = True

    def authorize():
        context = request.app.state.sessions.resolve(request.cookies.get(COOKIE))
        if context != request.state.context:
            raise HTTPException(401, "请重新登录。")

    async def body():
        try:
            async for frame in stream_run(store, run_id, authorize):
                yield frame
        finally:
            await release()

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        background=BackgroundTask(release),
    )


@app.get("/runs")
async def list_runs(request: Request, limit: int = Query(default=20, ge=1, le=50)):
    from .persistent_runs import PersistentRunStore

    store = request.state.runs
    return {
        "runs": store.history(limit),
        "limit": limit,
        "storage": "postgresql" if isinstance(store, PersistentRunStore) else "memory",
    }


@app.get("/runs/{run_id}/citations/{chunk_id}")
async def get_citation(run_id: UUID, chunk_id: UUID, request: Request):
    record = request.state.runs.get(run_id)
    if record is None:
        raise HTTPException(404, "运行或引用不存在，或当前身份无权访问。")
    for citation in record.get("citations", []):
        if citation.get("chunk_id") == str(chunk_id):
            return citation
    raise HTTPException(404, "运行或引用不存在，或当前身份无权访问。")
