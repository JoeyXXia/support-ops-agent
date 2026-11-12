import { useEffect, useState } from "react";
import type { FormEvent } from "react";

type Run = {
  id: string;
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
    step: number;
    tool?: string;
    call_id?: string;
    result?: { ok: boolean };
  }>;
};

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    signal: AbortSignal.timeout(8000),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(
      typeof detail?.detail === "string"
        ? detail.detail
        : `请求失败（HTTP ${response.status}），请检查服务或输入。`,
    );
  }
  return response.json() as Promise<T>;
}

export function App() {
  const [question, setQuestion] = useState(
    "CloudDesk ws-alpha升级后一直返回429，帮我排查原因。",
  );
  const [ticketDraft, setTicketDraft] = useState(false);
  const [knowledge, setKnowledge] = useState(false);
  const [version, setVersion] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (!run || !["queued", "running"].includes(run.status) || error) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const startedAt = Date.now();
    const id = run.id;
    async function poll() {
      try {
        const current = await api<Run>(`/runs/${id}`);
        if (!active) return;
        setRun(current);
        if (["queued", "running"].includes(current.status)) {
          if (Date.now() - startedAt >= 180000)
            throw new Error("等待已达3分钟，请重新查询该运行。");
          timer = setTimeout(poll, 500);
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "读取运行失败。");
      }
    }
    timer = setTimeout(poll, 100);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [run?.id, retry, error]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !question.trim()) return;
    setBusy(true);
    setError("");
    setRun(null);
    try {
      const created = await api<Run>("/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          knowledge,
          ticket_draft: ticketDraft,
          product_version: knowledge ? version || null : null,
        }),
      });
      setRun(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : "提交失败。");
    } finally {
      setBusy(false);
    }
  }
  async function decide(approve: boolean) {
    if (!run?.proposed_action?.payload_digest || busy) return;
    setBusy(true);
    setError("");
    try {
      const current = await api<Run>(
        `/approvals/${run.proposed_action.approval_id}/${approve ? "approve" : "reject"}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            run_id: run.id,
            payload_digest: run.proposed_action.payload_digest,
          }),
        },
      );
      setRun(current);
      setRetry((value) => value + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "审批请求失败。");
    } finally {
      setBusy(false);
    }
  }

  const running = !!run && ["queued", "running"].includes(run.status);
  const labels: Record<string, string> = {
    queued: "等待执行",
    running: "运行中",
    completed: "完成",
    awaiting_approval: "等待审批",
    failed: "失败",
    stopped: "已停止",
  };
  return (
    <main>
      <header>
        <span className="brand">SupportOps Agent</span>
        <span className="badge">本地 mock</span>
      </header>
      <section className="intro">
        <p className="eyebrow">CLOUDDESK / 技术支持</p>
        <h1>
          让每一次排障，
          <br />
          都有据可查。
        </h1>
        <p className="summary">输入问题，查看只读工具步骤和排障结果。</p>
      </section>
      <section className="card">
        <h2>{knowledge ? "文档引用演示" : "429 排障演示"}</h2>
        <p className="muted">
          {knowledge
            ? "可查询配额、升级、401、分钟限流或月额度的固定示例。"
            : "当前为固定 ws-alpha 场景的确定性 mock。"}
        </p>
        <form onSubmit={submit}>
          <label>
            <input
              type="checkbox"
              checked={knowledge}
              onChange={(event) => {
                setKnowledge(event.target.checked);
                setTicketDraft(false);
              }}
            />
            使用文档引用演示
          </label>
          <label>
            <input
              type="checkbox"
              checked={ticketDraft}
              onChange={(event) => {
                setTicketDraft(event.target.checked);
                setKnowledge(false);
              }}
            />
            生成待审核工单草稿（需启用持久化模式）
          </label>
          {knowledge && (
            <label>
              产品版本
              <select
                value={version}
                onChange={(event) => setVersion(event.target.value)}
              >
                <option value="">请选择版本</option>
                <option value="1.0">1.0</option>
                <option value="2.0">2.0</option>
              </select>
            </label>
          )}
          <label htmlFor="question">你的问题</label>
          <textarea
            id="question"
            value={question}
            maxLength={2000}
            rows={3}
            onChange={(e) => setQuestion(e.target.value)}
            required
          />
          <button disabled={busy || running || !question.trim()}>
            {busy ? "提交中…" : running ? "排查中…" : "开始排查"}
          </button>
        </form>
      </section>
      {error && (
        <section className="card" role="alert">
          <p className="error">{error}</p>
          {run && (
            <button
              onClick={() => {
                setError("");
                setRetry((v) => v + 1);
              }}
            >
              重新查询当前运行
            </button>
          )}
        </section>
      )}
      {run && (
        <section className="card" aria-live="polite">
          <h2>运行：{labels[run.status] || run.status}</h2>
          <p className="muted">
            ID：{run.id} · {run.mode}
          </p>
          <ol>
            {run.events.map((e, i) => (
              <li key={i}>
                步骤 {e.step} ·{" "}
                {e.kind === "tool.finished"
                  ? `工具 ${e.tool} ${e.result?.ok === false ? "失败" : "完成"}（${e.call_id}）`
                  : e.kind === "model.error"
                    ? "模型步骤失败"
                    : "模型步骤完成"}
              </li>
            ))}
          </ol>
          {run.answer && <p className="answer">{run.answer}</p>}
          {run.proposed_action && (
            <section className="action-draft" aria-label="待审核工单草稿">
              <h3>待审核工单草稿</h3>
              <dl>
                <dt>目标租户</dt>
                <dd>{run.proposed_action.tenant_id}</dd>
                <dt>工作区</dt>
                <dd>{run.proposed_action.params.workspace_id}</dd>
                <dt>动作</dt>
                <dd>{run.proposed_action.action}</dd>
                <dt>标题</dt>
                <dd>{run.proposed_action.params.title}</dd>
                <dt>内容</dt>
                <dd style={{ whiteSpace: "pre-wrap" }}>
                  {run.proposed_action.params.description}
                </dd>
                <dt>状态</dt>
                <dd>{run.proposed_action.status}</dd>
                {run.proposed_action.expires_at && (
                  <>
                    <dt>审批有效期至（北京时间）</dt>
                    <dd>
                      {new Date(run.proposed_action.expires_at).toLocaleString(
                        "zh-CN",
                        { timeZone: "Asia/Shanghai" },
                      )}
                    </dd>
                  </>
                )}
              </dl>
              {run.status === "awaiting_approval" &&
                run.proposed_action.payload_digest && (
                  <>
                    <button disabled={busy} onClick={() => decide(true)}>
                      批准并创建工单
                    </button>{" "}
                    <button disabled={busy} onClick={() => decide(false)}>
                      拒绝草稿
                    </button>
                  </>
                )}
            </section>
          )}
          {run.ticket && (
            <p>
              工单 ID：{run.ticket.ticket_id} · {run.ticket.title}
            </p>
          )}
          {run.citations?.map((citation) => (
            <details key={citation.chunk_id}>
              <summary>
                {citation.source} · 产品 {citation.product_version} · 修订{" "}
                {citation.version_number}
              </summary>
              <p>
                {citation.source_path} · 第 {citation.start_line}–
                {citation.end_line} 行
              </p>
              <p style={{ whiteSpace: "pre-wrap" }}>{citation.body}</p>
            </details>
          ))}
          {["failed", "stopped"].includes(run.status) && (
            <p className="error">
              执行已结束：{run.reason}。请核对问题或联系开发者。
            </p>
          )}
        </section>
      )}
      <footer>
        CloudDesk为合成演示。使用固定本地演示身份。当前服务仅用于本机开发。
      </footer>
    </main>
  );
}
