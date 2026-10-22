import { useEffect, useState } from "react";

type HealthState =
  | { kind: "loading" }
  | { kind: "success" }
  | { kind: "error"; message: string };

export function App() {
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 5000);
    let active = true;

    async function checkHealth() {
      try {
        const response = await fetch("/api/health", {
          signal: controller.signal,
          cache: "no-store",
        });
        if (!response.ok)
          throw new Error(`健康检查失败（HTTP ${response.status}）。`);
        const body: unknown = await response.json();
        if (
          typeof body !== "object" ||
          body === null ||
          !("status" in body) ||
          body.status !== "ok" ||
          !("service" in body) ||
          body.service !== "supportops-api"
        ) {
          throw new Error("服务返回了无法识别的健康状态。");
        }
        if (active) setHealth({ kind: "success" });
      } catch (error) {
        if (active) {
          setHealth({
            kind: "error",
            message: controller.signal.aborted
              ? "连接超时，请检查后端是否启动。"
              : error instanceof Error
                ? error.message
                : "无法连接后端服务。",
          });
        }
      } finally {
        window.clearTimeout(timeout);
      }
    }
    void checkHealth();
    return () => {
      active = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [attempt]);

  function retry() {
    setHealth({ kind: "loading" });
    setAttempt((value) => value + 1);
  }

  return (
    <main>
      <header>
        <span className="brand">SupportOps Agent</span>
        <span className="badge">Day 1 · 工程起点</span>
      </header>
      <section className="intro">
        <p className="eyebrow">CLOUDDESK / 技术支持</p>
        <h1>
          让每一次排障，
          <br />
          都有据可查。
        </h1>
        <p className="summary">
          从 API 429
          问题开始，逐步串起状态查询、文档检索与人工批准后的工单创建。
        </p>
      </section>
      <section className="card" aria-labelledby="scenario-title">
        <p className="eyebrow">主场景</p>
        <h2 id="scenario-title">升级后，API 一直返回 429</h2>
        <p>“帮我查原因；解决不了就建个技术支持工单。”</p>
        <p className="muted">
          当前仅完成服务联通；排障对话、工具调用和建单将在后续开发。
        </p>
      </section>
      <section className="card health" aria-labelledby="health-title">
        <div>
          <h2 id="health-title">后端连接</h2>
          <div role="status" aria-live="polite" aria-atomic="true">
            {health.kind === "loading" && <p>正在检查服务…</p>}
            {health.kind === "success" && (
              <p className="success">● 服务正常 · supportops-api</p>
            )}
            {health.kind === "error" && (
              <p className="error">{health.message} 请确认服务已启动后重试。</p>
            )}
          </div>
        </div>
        <button onClick={retry} disabled={health.kind === "loading"}>
          {health.kind === "loading" ? "检查中…" : "重新检查"}
        </button>
      </section>
      <footer>
        CloudDesk 是虚构的 SaaS 产品。本项目为合成场景演示，未接入商业生产系统。
      </footer>
    </main>
  );
}
