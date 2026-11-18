import { useEffect, useState } from "react";
import type { Run } from "./runEvents";
import {
  citationHash,
  creationLabel,
  runHash,
  statusLabel,
} from "./navigation";
import { api } from "./api";

export function Navigation() {
  return (
    <nav aria-label="项目导航">
      <a href="#">新建排查</a>
      <a href="#history">历史运行</a>
    </nav>
  );
}

type Summary = Pick<
  Run,
  "id" | "question" | "status" | "reason" | "created_at"
>;
export function HistoryPage() {
  const [history, setHistory] = useState<{
    runs: Summary[];
    storage: string;
  } | null>(null);
  const [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    api<{ runs: Summary[]; storage: string }>("/runs?limit=20")
      .then((value) => {
        if (active) setHistory(value);
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : "读取历史失败。");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [refresh]);
  return (
    <main>
      <Navigation />
      <section className="card">
        <h1>历史运行</h1>
        <p>仅显示当前账号最近的20条记录。</p>
        {history?.storage === "memory" && (
          <p>当前为内存模式，服务重启后历史会丢失；持久模式可保留历史。</p>
        )}
        <button disabled={loading} onClick={() => setRefresh((v) => v + 1)}>
          {loading ? "正在读取历史…" : "刷新历史"}
        </button>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {!loading && !error && history?.runs.length === 0 && (
          <p>当前账号还没有历史运行。</p>
        )}
        <ul className="history-list">
          {history?.runs.map((row) => (
            <li key={row.id}>
              <a href={runHash(row.id)}>{row.question}</a>
              <p>
                {statusLabel(row.status)} · {creationLabel(row.created_at)}
              </p>
              <small>运行 ID：{row.id}</small>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

export function CitationPanel({
  run,
  citationId,
}: {
  run: Run;
  citationId?: string;
}) {
  const [citation, setCitation] = useState<
    NonNullable<Run["citations"]>[number] | null
  >(null);
  const [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  useEffect(() => {
    let active = true;
    setCitation(null);
    setError("");
    if (!citationId)
      return () => {
        active = false;
      };
    setLoading(true);
    api<NonNullable<Run["citations"]>[number]>(
      `/runs/${run.id}/citations/${citationId}`,
    )
      .then((value) => {
        if (active) setCitation(value);
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : "读取引用失败。");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [run.id, citationId]);
  return (
    <aside className="card citation-panel" aria-label="文档引用">
      <h2>文档引用</h2>
      {!run.citations?.length && <p>本次没有经核验的文档引用。</p>}
      <ul>
        {run.citations?.map((row) => (
          <li key={row.chunk_id}>
            <a
              href={citationHash(run.id, row.chunk_id)}
              aria-current={row.chunk_id === citationId ? "page" : undefined}
            >
              查看引用：{row.source}
            </a>
          </li>
        ))}
      </ul>
      {loading && <p>正在读取引用…</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {citation && (
        <section aria-label="引用原文">
          <h3>{citation.source}</h3>
          <p>
            产品 {citation.product_version} · 修订 {citation.version_number}
          </p>
          <p>
            {citation.source_path} · 第 {citation.start_line}–
            {citation.end_line} 行
          </p>
          <p className="citation-body">{citation.body}</p>
        </section>
      )}
    </aside>
  );
}

type Ticket = {
  ticket_id: string;
  tenant_id: string;
  workspace_id: string;
  title: string;
  description: string;
  status: string;
};
export function TicketPage({ id }: { id: string }) {
  const [ticket, setTicket] = useState<Ticket | null>(null),
    [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    api<Ticket>(`/tickets/${id}`)
      .then((value) => {
        if (active) setTicket(value);
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : "读取工单失败。");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [id]);
  return (
    <main>
      <Navigation />
      <section className="card">
        <h1>工单详情</h1>
        {loading && <p>正在读取工单…</p>}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {ticket && (
          <>
            <h2>{ticket.title}</h2>
            <dl>
              <dt>工单 ID</dt>
              <dd>{ticket.ticket_id}</dd>
              <dt>租户</dt>
              <dd>{ticket.tenant_id}</dd>
              <dt>工作区</dt>
              <dd>{ticket.workspace_id}</dd>
              <dt>当前状态</dt>
              <dd>{ticket.status === "open" ? "待处理" : "已关闭"}</dd>
            </dl>
            <p className="citation-body">{ticket.description}</p>
          </>
        )}
      </section>
    </main>
  );
}
