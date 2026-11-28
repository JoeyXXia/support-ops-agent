"""Owner-visible operational diagnosis without prompts, tool payloads or private ticket data."""

from sqlalchemy import text

from packages.agent.trace import CODES, safe_code, safe_id, timed

from .events import PUBLIC_KINDS, public_event


def run_diagnostics(record, engine):
    status = record["status"]
    events = [
        public_event(event) for event in record.get("events", []) if event["kind"] in PUBLIC_KINDS
    ]
    for event in events:
        if "reason" in event:
            event["reason"] = safe_code(event["reason"])
    failure = None
    if status in {"failed", "stopped", "cancelled", "interrupted"}:
        reason = record.get("reason") or ""
        code = safe_code(reason if reason in CODES else reason.removeprefix("tool_"))
        error_event = next(
            (e for e in reversed(events) if e["kind"].endswith(".error") and e.get("code") == code),
            None,
        )
        stopped = next((e for e in reversed(events) if e["kind"] == "run.stopped"), None)
        evidence = error_event or stopped or (events[-1] if events else {})
        operation = None
        if error_event:
            operation = (
                "retrieval"
                if evidence.get("tool") == "search_docs"
                else evidence["kind"].split(".")[0]
            )
        failure = {
            "error_code": code,
            "step": evidence.get("step"),
            "step_id": evidence.get("step_id"),
            "operation": operation,
        }
    write = {"draft_saved": None, "ticket_receipt": "unverified"}
    if engine is not None:
        with timed("database.trace_receipt"), engine.connect() as conn:
            found = conn.execute(
                text("""SELECT a.id AS approval_id, e.approval_id AS receipt_id
                        FROM agent_runs r LEFT JOIN approvals a ON a.run_id=r.id
                        LEFT JOIN action_executions e ON e.approval_id=a.id
                        WHERE r.id=:id"""),
                {"id": record["id"]},
            ).one()
        write = {
            "draft_saved": found.approval_id is not None,
            "ticket_receipt": "committed" if found.receipt_id is not None else "no_receipt",
        }
    return {
        "run_id": record["id"],
        "request_id": safe_id(record.get("request_id")),
        "status": status,
        "failure": failure,
        "write": write,
        "events": events,
    }
