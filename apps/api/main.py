import os
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .runs import RunInput, RunStore


@asynccontextmanager
async def lifespan(app):
    async with AsyncExitStack() as stack:
        if os.getenv("SUPPORTOPS_PERSIST_RUNS") == "1":
            from packages.agent.persistence import checkpoint_saver
            from packages.storage.database import engine_from_environment

            from .persistent_runs import PersistentRunStore

            engine = engine_from_environment()
            stack.callback(engine.dispose)
            saver = await stack.enter_async_context(checkpoint_saver(engine))
            app.state.runs = PersistentRunStore(engine, saver)
        else:
            app.state.runs = RunStore()
        try:
            yield
        finally:
            await app.state.runs.close()


app = FastAPI(title="SupportOps Agent", version="0.1.0", lifespan=lifespan)


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
    try:
        return request.app.state.runs.create(
            body.question,
            knowledge=body.knowledge,
            product_version=body.product_version,
            ticket_draft=body.ticket_draft,
        )
    except ValueError:
        raise HTTPException(status_code=409, detail="工单草稿需要启用持久化运行模式。") from None
    except OverflowError:
        raise HTTPException(status_code=503, detail="演示运行已满，请稍后重试。") from None


@app.get("/runs/{run_id}")
async def get_run(run_id: UUID, request: Request):
    record = request.app.state.runs.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="运行不存在或当前身份无权访问。")
    return record


@app.post("/runs/{run_id}/resume", status_code=202)
async def resume_run(run_id: UUID, request: Request):
    from .persistent_runs import PersistentRunStore

    store = request.app.state.runs
    if not isinstance(store, PersistentRunStore):
        raise HTTPException(status_code=409, detail="请启用持久化运行模式。")
    try:
        return store.resume(run_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="运行不存在或当前身份无权访问。") from None
    except OverflowError:
        raise HTTPException(status_code=409, detail="运行正在执行或已达到并发上限。") from None


class ExecuteTicketInput(BaseModel):
    model_config = {"extra": "forbid"}
    approval_id: UUID
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)
    payload_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@app.post("/tickets", status_code=201)
async def create_ticket(body: ExecuteTicketInput, request: Request):
    from packages.storage.tickets import ApprovalConflict, TicketRepository

    from .persistent_runs import PersistentRunStore

    store = request.app.state.runs
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

    store = request.app.state.runs
    if not isinstance(store, PersistentRunStore):
        raise HTTPException(status_code=409, detail="请启用持久化运行模式。")
    try:
        TicketRepository(store.repository.engine).decide(
            approval_id,
            store.context,
            run_id=body.run_id,
            digest=body.payload_digest,
            approve=approve,
        )
        return store.resume(body.run_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="审批不存在或不可访问。") from None
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
