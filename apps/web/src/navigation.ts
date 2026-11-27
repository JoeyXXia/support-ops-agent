export type Route =
  | { kind: "compose" }
  | { kind: "history" }
  | { kind: "invalid" }
  | { kind: "run"; id: string }
  | { kind: "citation"; id: string; citationId: string }
  | { kind: "ticket"; id: string };
const uuid = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
export function parseRoute(hash: string): Route {
  if (!hash || hash === "#") return { kind: "compose" };
  if (hash === "#history") return { kind: "history" };
  const citation = hash.match(
    new RegExp(`^#run/(${uuid})/citation/(${uuid})$`, "i"),
  );
  if (citation)
    return {
      kind: "citation",
      id: citation[1].toLowerCase(),
      citationId: citation[2].toLowerCase(),
    };
  const run = hash.match(new RegExp(`^#run/(${uuid})$`, "i"));
  if (run) return { kind: "run", id: run[1].toLowerCase() };
  const ticket = hash.match(new RegExp(`^#ticket/(${uuid})$`, "i"));
  if (ticket) return { kind: "ticket", id: ticket[1].toLowerCase() };
  return { kind: "invalid" };
}
export const runHash = (id: string) => `#run/${encodeURIComponent(id)}`;
export const citationHash = (id: string, chunk: string) =>
  `${runHash(id)}/citation/${encodeURIComponent(chunk)}`;
export const ticketHash = (id: string) => `#ticket/${encodeURIComponent(id)}`;
export function creationLabel(value?: string | null): string {
  if (!value || !Number.isFinite(new Date(value).getTime()))
    return "创建时间未记录";
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
}
export const statusLabel = (status: string) =>
  (
    ({
      queued: "等待执行",
      running: "运行中",
      completed: "完成",
      awaiting_approval: "等待审批",
      interrupted: "需要显式恢复",
      failed: "失败",
      stopped: "已停止",
      cancelled: "已取消",
    }) as Record<string, string>
  )[status] || status;
