import test from "node:test";
import assert from "node:assert/strict";
import {
  parseRoute,
  runHash,
  citationHash,
  ticketHash,
  creationLabel,
} from "../src/navigation.ts";
const run = "11111111-1111-4111-8111-111111111111";
const chunk = "22222222-2222-4222-8222-222222222222";
test("refreshable run, citation and ticket URLs retain scoped identifiers", () => {
  assert.deepEqual(parseRoute(runHash(run)), { kind: "run", id: run });
  assert.deepEqual(parseRoute(citationHash(run, chunk)), {
    kind: "citation",
    id: run,
    citationId: chunk,
  });
  assert.deepEqual(parseRoute(ticketHash(chunk)), {
    kind: "ticket",
    id: chunk,
  });
  assert.deepEqual(parseRoute("#history"), { kind: "history" });
  assert.deepEqual(parseRoute(""), { kind: "compose" });
});
test("paths, malformed UUIDs and arbitrary schemes never become API resource routes", () => {
  for (const hash of [
    "#run/../../.env",
    "#run/" + run + "/citation/..%2f.env",
    "#ticket/javascript:alert(1)",
    "#run/not-a-uuid",
    "#history?user=other",
    citationHash(run, "../../.env"),
  ])
    assert.equal(parseRoute(hash).kind, "invalid");
});
test("legacy or invalid time does not invent a date", () => {
  for (const value of [null, undefined, "bad date", ""])
    assert.equal(creationLabel(value), "创建时间未记录");
  assert.notEqual(creationLabel("2026-01-01T00:00:00+00:00"), "创建时间未记录");
});
