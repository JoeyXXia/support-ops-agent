"""Bounded snapshot-to-SSE projection; no durable cursor or automatic replay protocol."""

import asyncio
import json
import time
from copy import deepcopy

from fastapi import HTTPException

PUBLIC_KINDS = {
    "step.started",
    "tool.started",
    "model.finished",
    "tool.finished",
    "model.error",
    "tool.error",
    "write.error",
    "retry.scheduled",
    "run.stopped",
}
PUBLIC_FIELDS = {
    "step",
    "kind",
    "tool",
    "call_id",
    "operation",
    "code",
    "reason",
    "attempt",
    "delay_seconds",
    "model_calls",
    "tool_calls",
    "write_calls",
    "retryable",
}
SETTLED = {"completed", "failed", "stopped", "interrupted", "awaiting_approval"}


def public_event(event):
    data = {k: v for k, v in event.items() if k in PUBLIC_FIELDS}
    if event["kind"] == "tool.finished":
        data["result"] = {"ok": event.get("result", {}).get("ok", False)}
    return data


def public_record(record):
    allowed = {
        "id",
        "created_at",
        "read_tool_transport",
        "question",
        "ticket_draft",
        "mode",
        "status",
        "reason",
        "answer",
        "citations",
        "state_path",
        "model_calls",
        "tool_calls",
        "write_calls",
        "steps",
        "proposed_action",
        "ticket",
    }
    result = {k: v for k, v in record.items() if k in allowed}
    result["events"] = [public_event(e) for e in record["events"] if e["kind"] in PUBLIC_KINDS]
    return result


class Projection:
    def __init__(self, run_id):
        self.run_id = str(run_id)
        self.seq = self.index = 0
        self.snapshot_sent = self.started = self.done = False

    def frame(self, kind, data):
        self.seq += 1
        event = {"seq": self.seq, "type": kind, "run_id": self.run_id, "data": data}
        return f"id: {self.seq}\nevent: {kind}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    def frames(self, record):
        if self.done:
            return
        record = deepcopy(record)
        visible = public_record(record)
        if not self.snapshot_sent:
            self.snapshot_sent = True
            yield self.frame("run.snapshot", {"run": visible})
        if not self.started and (record["status"] != "queued" or record["events"]):
            self.started = True
            yield self.frame(
                "run.started",
                {"status": record["status"], "historical": record["status"] in SETTLED},
            )
        events = record["events"]
        if self.index > len(events):
            self.done = True
            yield self.frame("stream.error", {"code": "history_changed"})
            return
        for event in events[self.index :]:
            if event["kind"] in PUBLIC_KINDS:
                data = public_event(event)
                yield self.frame(event["kind"], data)
        self.index = len(events)
        if record["status"] in SETTLED:
            self.done = True
            if record.get("answer"):
                # The model already produced this answer; this is NOT token streaming.
                yield self.frame("answer.delta", {"text": record["answer"], "complete": True})
            kind = (
                "approval.required" if record["status"] == "awaiting_approval" else "run.finished"
            )
            yield self.frame(kind, {"run": visible})


async def stream_run(store, run_id, authorize, *, interval=0.1, timeout=180, heartbeat=10):
    projection = Projection(run_id)
    started = ping = time.monotonic()
    while True:
        try:
            authorize()
        except HTTPException:
            yield projection.frame("stream.error", {"code": "unauthorized"})
            return
        if time.monotonic() - started >= timeout:
            yield projection.frame("stream.error", {"code": "stream_timeout"})
            return
        try:
            record = store.get(run_id)
            if record is None:
                yield projection.frame("stream.error", {"code": "run_unavailable"})
                return
            for frame in projection.frames(record):
                try:
                    authorize()
                except HTTPException:
                    yield projection.frame("stream.error", {"code": "unauthorized"})
                    return
                yield frame
        except Exception:
            yield projection.frame("stream.error", {"code": "stream_unavailable"})
            return
        if projection.done:
            return
        if time.monotonic() - ping >= heartbeat:
            yield ": keep-alive\n\n"
            ping = time.monotonic()
        await asyncio.sleep(interval)
