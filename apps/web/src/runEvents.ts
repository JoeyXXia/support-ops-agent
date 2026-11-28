export type Run = {
  id: string;
  created_at?: string;
  question: string;
  mode: string;
  status: string;
  reason: string;
  ticket?: { ticket_id: string; title: string } | null;
  proposed_action?: {
    approval_id: string;
    payload_digest?: string | null;
    expires_at?: string | null;
    tenant_id: string;
    action: string;
    status: string;
    params: { workspace_id: string; title: string; description: string };
  } | null;
  answer: string;
  citations?: Array<{
    chunk_id: string;
    source: string;
    source_path: string;
    product_version: string;
    version_number: number;
    start_line: number;
    end_line: number;
    body: string;
  }>;
  events: Array<{
    kind: string;
    code?: string;
    reason?: string;
    attempt?: number;
    delay_seconds?: number;
    operation?: string;
    model_calls?: number;
    tool_calls?: number;
    write_calls?: number;
    step: number;
    tool?: string;
    call_id?: string;
    result?: { ok: boolean };
  }>;
};

export const streamTypes = [
  "run.snapshot",
  "run.started",
  "step.started",
  "tool.started",
  "model.finished",
  "tool.finished",
  "model.error",
  "tool.error",
  "write.error",
  "retry.scheduled",
  "run.stopped",
  "answer.delta",
  "approval.required",
  "run.finished",
  "stream.error",
] as const;
export type StreamEvent = {
  seq: number;
  type: string;
  run_id: string;
  data: Record<string, unknown>;
};
export type View = {
  run: Run | null;
  error: string;
  seq: number;
  transport: "idle" | "connecting" | "open" | "closed" | "error";
};
export const initialView: View = {
  run: null,
  error: "",
  seq: 0,
  transport: "idle",
};
export type Action =
  | { type: "loaded"; run: Run | null }
  | { type: "error"; message: string }
  | { type: "connect" }
  | { type: "reconnect" }
  | { type: "open" }
  | { type: "event"; event: StreamEvent };

export function decodeEvent(
  text: string,
  type: string,
  runId: string,
): StreamEvent {
  const value = JSON.parse(text);
  if (
    !value ||
    value.type !== type ||
    value.run_id !== runId ||
    !streamTypes.includes(type as (typeof streamTypes)[number]) ||
    !Number.isSafeInteger(value.seq) ||
    value.seq < 1 ||
    !value.data ||
    typeof value.data !== "object" ||
    Array.isArray(value.data)
  )
    throw new Error("Invalid stream envelope");
  if (["run.snapshot", "run.finished", "approval.required"].includes(type)) {
    const run = value.data.run;
    if (
      !run ||
      run.id !== runId ||
      typeof run.status !== "string" ||
      !Array.isArray(run.events)
    )
      throw new Error("Invalid run snapshot");
  }
  return value;
}

export function runReducer(state: View, action: Action): View {
  if (action.type === "loaded") return { ...initialView, run: action.run };
  if (action.type === "connect")
    return { ...state, error: "", seq: 0, transport: "connecting" };
  if (action.type === "reconnect") return { ...state, transport: "connecting" };
  if (action.type === "open") return { ...state, transport: "open" };
  if (action.type === "error")
    return {
      ...state,
      error: action.message,
      transport: action.message ? "error" : "idle",
    };
  const event = action.event;
  if (!state.run || event.run_id !== state.run.id || event.seq <= state.seq)
    return state;
  const next = { ...state, seq: event.seq };
  if (event.type === "stream.error")
    return {
      ...next,
      transport: "error",
      error: `${({ unauthorized: "登录已失效", stream_timeout: "实时反馈等待超时", run_unavailable: "运行暂不可读取", history_changed: "事件记录发生变化", stream_unavailable: "读取实时进度失败" } as Record<string, string>)[String(event.data.code)] || "实时连接中断"}；后台任务可能继续，请显式重新查询当前运行。`,
    };
  const terminal = ["completed", "failed", "stopped", "cancelled"].includes(
    state.run.status,
  );
  if (terminal) return next;
  if (["approval.required", "run.finished"].includes(event.type))
    return { ...next, run: event.data.run as Run, transport: "closed" };
  if (event.type === "run.snapshot")
    return {
      ...next,
      run:
        event.data.replay === true
          ? (event.data.run as Run)
          : { ...(event.data.run as Run), events: [], answer: "" },
    };
  if (event.type === "answer.delta")
    return {
      ...next,
      run: {
        ...state.run,
        answer: state.run.answer + String(event.data.text || ""),
      },
    };
  if (event.type === "run.started")
    return event.data.status === "running"
      ? { ...next, run: { ...state.run, status: "running" } }
      : next;
  return {
    ...next,
    run: {
      ...state.run,
      events: [
        ...state.run.events,
        {
          ...event.data,
          kind: event.type,
          step: Number(event.data.step || 0),
        } as Run["events"][number],
      ],
    },
  };
}
