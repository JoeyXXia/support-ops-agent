"""Day 1 liveness endpoint; no external services are required."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="SupportOps Agent", version="0.1.0")


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["supportops-api"] = "supportops-api"


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    """Report API liveness, not database/model readiness."""
    return HealthResponse()
