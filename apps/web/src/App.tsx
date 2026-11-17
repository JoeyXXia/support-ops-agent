import { initialView, runReducer, decodeEvent, streamTypes } from "./runEvents";
import type { Run } from "./runEvents";
import { useEffect, useReducer, useState } from "react";
import type { FormEvent } from "react";

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    signal: AbortSignal.timeout(8000),
  });
  if (!response.ok) {
    if (response.status === 401)
      window.dispatchEvent(new Event("supportops-unauthorized"));
    const detail = await response.json().catch(() => null);
    throw new Error(
      typeof detail?.detail === "string"
        ? detail.detail
        : `请求失败（HTTP ${response.status}），请检查服务或输入。`,
    );
  }
  return response.json() as Promise<T>;
}

function Workspace() {
  const [question, setQuestion] = useState(
    "CloudDesk ws-alpha升级后一直返回429，帮我排查原因。",
  );
  const [ticketDraft, setTicketDraft] = useState(false);
  const [knowledge, setKnowledge] = useState(false);
  const [version, setVersion] = useState("");
  const [view, dispatch] = useReducer(runReducer, initialView);
  const { run, error } = view;
  const setRun = (value: Run | null) =>
    dispatch({ type: "loaded", run: value });
  const setError = (message: string) => dispatch({ type: "error", message });
  const [busy, setBusy] = useState(false);

  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (!run || !["queued", "running"].includes(run.status)) return;
    let active = true;
    let ended = false;
    const id = run.id;
    dispatch({ type: "connect" });
    const source = new EventSource(`/api/runs/${id}/events`);
    const watchdog = setTimeout(() => {
      if (!active || ended) return;
      ended = true;
      clearTimeout(watchdog);
      source.close();
      setError(
        "实时反馈等待已达3分钟；后台任务可能继续，请显式重新查询当前运行。",
      );
    }, 180000);

    source.onopen = () => {
      if (active) dispatch({ type: "open" });
    };
    for (const type of streamTypes) {
      source.addEventListener(type, (message) => {
        if (!active || ended) return;
        try {
          const event = decodeEvent((message as MessageEvent).data, type, id);
          dispatch({ type: "event", event });
          if (
            ["run.finished", "approval.required", "stream.error"].includes(type)
          ) {
            ended = true;
            clearTimeout(watchdog);
            source.close();
          }
          if (type === "stream.error" && event.data.code === "unauthorized")
            window.dispatchEvent(new Event("supportops-unauthorized"));
        } catch {
          ended = true;
          clearTimeout(watchdog);
          source.close();
          setError("实时事件格式错误，请重新查询当前运行。");
        }
      });
    }
    source.onerror = () => {
      if (!active || ended) return;
      ended = true;
      clearTimeout(watchdog);
      source.close();
      setError("实时连接已中断；后台任务可能继续，请显式重新查询当前运行。");
      // One auth probe exposes a 401 to the existing login boundary; never reconnect SSE here.
      void api("/session").catch(() => {});
    };
    return () => {
      active = false;
      ended = true;
      clearTimeout(watchdog);
      source.close();
    };
  }, [run?.id, retry]);

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

  const running =
    !!run &&
    ["queued", "running"].includes(run.status) &&
    view.transport !== "error";
  const labels: Record<string, string> = {
    queued: "等待执行",
    running: "运行中",
    completed: "完成",
    awaiting_approval: "等待审批",
    interrupted: "需要显式恢复",
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
            ID：{run.id} · {run.mode} · 实时连接：
            {
              {
                idle: "未连接",
                connecting: "连接中",
                open: "已连接",
                closed: "已结束",
                error: "已中断",
              }[view.transport]
            }
          </p>
          <ol>
            {run.events.map((e, i) => (
              <li key={i}>
                步骤 {e.step} · {describeEvent(e)}
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
        CloudDesk为合成演示。身份来自已登录的种子账号。当前服务仅用于本机开发。
      </footer>
    </main>
  );
}

function describeEvent(event: Run["events"][number]): string {
  if (event.kind === "run.stopped")
    return `停止：${event.reason}，模型 ${event.model_calls} 次、工具 ${event.tool_calls} 次、写入 ${event.write_calls} 次`;
  if (event.kind === "step.started") return "正在处理当前步骤";
  if (event.kind === "tool.started") return `正在调用工具 ${event.tool}`;
  if (event.kind === "retry.scheduled")
    return `${event.tool || event.operation} 第 ${event.attempt} 次失败，${event.delay_seconds} 秒后重试`;
  if (event.kind.endsWith(".error"))
    return `${event.tool || event.kind.split(".")[0]} 失败：${event.code}（尝试 ${event.attempt || 1}）`;
  if (event.kind === "tool.finished")
    return `工具 ${event.tool} ${event.result?.ok === false ? "失败" : "完成"}（${event.call_id}）`;
  return event.kind === "model.finished" ? "模型步骤完成" : event.kind;
}

type Principal = { user_id: string; tenant_id: string; role: string };

export function App() {
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [loading, setLoading] = useState(true);
  const [username, setUsername] = useState("user-alpha");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  useEffect(() => {
    const clear = () => setPrincipal(null);
    window.addEventListener("supportops-unauthorized", clear);
    return () => window.removeEventListener("supportops-unauthorized", clear);
  }, []);
  useEffect(() => {
    let active = true;
    api<Principal>("/session")
      .then((p) => {
        if (active) setPrincipal(p);
      })
      .catch(() => {})
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);
  async function login(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      setPrincipal(
        await api<Principal>("/session", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password }),
        }),
      );
      setPassword("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "登录失败。");
    } finally {
      setPending(false);
    }
  }
  async function logout() {
    setPending(true);
    setError("");
    try {
      await api("/session", { method: "DELETE" });
      setPrincipal(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "退出失败。");
    } finally {
      setPending(false);
    }
  }
  if (loading)
    return (
      <main>
        <p>读取会话…</p>
      </main>
    );
  if (!principal)
    return (
      <main>
        <h1>SupportOps Agent 登录</h1>
        <p>本机种子账号使用管理员配置的独立口令。</p>
        <form onSubmit={login}>
          <label>
            账号
            <select
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            >
              {["user-alpha", "support-alpha", "user-beta", "support-beta"].map(
                (u) => (
                  <option key={u}>{u}</option>
                ),
              )}
            </select>
          </label>
          <label>
            口令
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <button disabled={pending}>登录</button>
        </form>
        {error && <p role="alert">{error}</p>}
      </main>
    );
  return (
    <>
      <div style={{ padding: "1rem" }}>
        <span>
          {principal.user_id} / {principal.tenant_id} / {principal.role}
        </span>
        <button onClick={logout} disabled={pending}>
          退出登录
        </button>
        {error && <p role="alert">{error}</p>}
      </div>
      <Workspace key={principal.user_id} />
    </>
  );
}
