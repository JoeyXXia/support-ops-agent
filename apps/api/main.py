from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from .runs import RunInput, RunStore


@asynccontextmanager
async def lifespan(app):
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
    try:
        return request.app.state.runs.create(
            body.question, knowledge=body.knowledge, product_version=body.product_version
        )
    except OverflowError:
        raise HTTPException(status_code=503, detail="演示运行已满，请稍后重试。") from None


@app.get("/runs/{run_id}")
async def get_run(run_id: UUID, request: Request):
    record = request.app.state.runs.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="运行不存在或已清理；重启会清空记录。")
    return record
