import test from "node:test";
import assert from "node:assert/strict";
import { initialView, runReducer, decodeEvent } from "../src/runEvents.ts";

const run = {
  id: "one",
  status: "queued",
  answer: "",
  events: [],
  mode: "mock",
  question: "synthetic",
  reason: "",
};
const event = (seq, type, data = {}) => ({ seq, type, run_id: "one", data });
const start = () => runReducer(initialView, { type: "loaded", run });
const apply = (state, e) => runReducer(state, { type: "event", event: e });

test("live events show step/tool progress before completion", () => {
  let s = runReducer(start(), { type: "connect" });
  s = apply(
    s,
    event(1, "run.snapshot", { run: { ...run, status: "running" } }),
  );
  s = apply(s, event(2, "step.started", { step: 1 }));
  s = apply(
    s,
    event(3, "tool.started", { step: 1, tool: "get_workspace_usage" }),
  );
  assert.equal(s.run.status, "running");
  assert.equal(s.run.events[1].kind, "tool.started");
  s = apply(
    s,
    event(4, "answer.delta", { text: "mock answer", complete: true }),
  );
  s = apply(
    s,
    event(5, "run.finished", {
      run: { ...run, status: "completed", answer: "mock answer" },
    }),
  );
  assert.equal(s.transport, "closed");
  assert.equal(s.run.answer, "mock answer");
});
test("duplicate and foreign frames never mutate current run", () => {
  const s = apply(start(), event(1, "step.started", { step: 1 }));
  assert.equal(apply(s, event(1, "step.started", { step: 1 })), s);
  assert.equal(apply(s, { ...event(2, "step.started"), run_id: "foreign" }), s);
});
test("approval closes phase, failure closes phase, stream error retains backend state", () => {
  let s = apply(
    start(),
    event(1, "approval.required", {
      run: { ...run, status: "awaiting_approval" },
    }),
  );
  assert.equal(s.transport, "closed");
  assert.equal(s.run.status, "awaiting_approval");
  s = apply(
    start(),
    event(1, "run.finished", {
      run: { ...run, status: "failed", reason: "internal_error" },
    }),
  );
  assert.equal(s.transport, "closed");
  assert.equal(s.run.status, "failed");
  s = apply(start(), event(1, "stream.error", { code: "stream_timeout" }));
  assert.equal(s.transport, "error");
  assert.equal(s.run.status, "queued");
});
test("explicit requery resets per-connection sequence and stale progress", () => {
  let s = apply(start(), event(10, "step.started", { step: 1 }));
  s = runReducer(s, { type: "connect" });
  assert.equal(s.seq, 0);
  s = apply(
    s,
    event(1, "run.snapshot", {
      run: { ...run, events: [{ kind: "step.started", step: 1 }] },
    }),
  );
  assert.equal(s.run.events.length, 0);
});
test("invalid envelopes, unexpected types and mismatched snapshot IDs rejected", () => {
  assert.throws(() => decodeEvent("bad json", "run.started", "one"));
  for (const e of [
    { ...event(1, "run.started"), seq: -1 },
    event(1, "internal.thought"),
    event(1, "run.snapshot", { run: { ...run, id: "foreign" } }),
  ]) {
    assert.throws(() => decodeEvent(JSON.stringify(e), e.type, "one"));
  }
  assert.equal(
    decodeEvent(JSON.stringify(event(1, "step.started")), "step.started", "one")
      .seq,
    1,
  );
});

test("replay snapshots replace progress and late frames cannot undo a ticket", () => {
  const snapshot = event(3, "run.snapshot", {
    replay: true,
    run: {
      ...run,
      status: "running",
      events: [{ kind: "tool.started", step: 1 }],
    },
  });
  let s = apply(start(), snapshot);
  assert.equal(s.run.events.length, 1);
  s = runReducer(s, { type: "reconnect" });
  assert.equal(s.seq, 3);
  assert.equal(apply(s, snapshot), s);
  const done = event(5, "run.finished", {
    replay: true,
    run: { ...run, status: "completed", ticket: { ticket_id: "one-ticket" } },
  });
  s = apply(s, done);
  assert.equal(apply(s, done), s);
  s = apply(s, { ...snapshot, seq: 6 });
  assert.equal(s.run.status, "completed");
  assert.equal(s.run.ticket.ticket_id, "one-ticket");
});
