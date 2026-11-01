# SupportOps Agent

为虚构 SaaS CloudDesk 提供有据可查的技术支持：从 API 429 排障出发，逐步实现文档引用、工具查询和人工批准后的工单创建。

**当前进度：Day 3 模型适配代码完成，真实调用被API余额阻塞。** 已有 FastAPI 健康接口和可检查后端连接的 React 页面。CloudDesk 是合成演示场景，未接入商业生产系统；已有独立模型适配与mock；尚无Agent loop、RAG、数据库或工单能力。健康检查和mock无需密钥；live模式会使用API计费。

## 快速启动（Windows PowerShell）

前置条件：Python 3.12+、Node.js 22.12+、npm、Git。本文的命令均从项目根目录执行，前后端分别使用一个终端。

```powershell
# 首次创建环境；已有 .venv 时跳过这一行
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
npm.cmd --prefix apps/web ci

# 可选配置：仅在 .env 不存在时复制，默认端口无需改动
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

本次机器没有系统 Python，已用 Codex 内置 Python 3.12.14 创建 `.venv`，之后可直接使用项目的 `.venv\Scripts\python.exe`。如果在这台机器需要重建：

```powershell
& 'C:\Users\27019\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv .venv
```

这个内置路径是当前机器的环境信息，不是项目依赖；其他机器安装自己的 Python 3.12 即可。

终端 A：

```powershell
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --reload
```

终端 B：

```powershell
npm.cmd --prefix apps/web run dev
```

打开 [前端页面](http://127.0.0.1:5173)，应显示“服务正常 · supportops-api”。[后端健康接口](http://127.0.0.1:8000/health) 返回：

```json
{"status":"ok","service":"supportops-api"}
```

接口文档：[Swagger UI](http://127.0.0.1:8000/docs)。终端按 Ctrl+C 停止对应服务。

## 请求如何到达后端

浏览器加载 React → 页面请求同源 `/api/health` → Vite 开发代理去掉 `/api` → 请求 `http://127.0.0.1:8000/health` → FastAPI 返回 JSON → 页面校验状态与服务名后显示结果。

页面会在 5 秒后停止等待，展示失败并允许重新检查。后端 `/health` 仅表示 API 进程存活，不证明数据库或模型可用。未增加跨域通配符配置。

根目录 `.env` 中的 `API_PROXY_TARGET` 只供 Vite 服务端代理读取，修改后重启前端。`.env` 与变体被 Git 忽略；不要将任何密钥放进浏览器代码或 `VITE_*` 变量。`.env.example` 是无凭据模板。

## 检查与故障验收

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m ruff check apps/api
.\.venv\Scripts\python.exe -m ruff format --check apps/api
npm.cmd --prefix apps/web run format:check
npm.cmd --prefix apps/web run typecheck
npm.cmd --prefix apps/web run build
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:5173/api/health
git check-ignore .env .env.local apps/web/.env.local
```

格式修复使用 `python -m ruff format apps/api`（请使用项目 Python）及 `npm.cmd --prefix apps/web run format`。Python 的直接依赖写在 `pyproject.toml`，完整安装版本记录在 `requirements.lock`；前端精确版本与完整解析分别记录在 `package.json`、`package-lock.json`。

故障演示：停掉后端，在页面点“重新检查”，应出现失败；重新启动后端，再点一次，应恢复正常。不能一直显示加载或沿用旧的成功状态。端口已占用时先检查已有进程；Vite 配置 `strictPort`，不会悄悄切换端口。

`npm run build` 仅验证可生成前端产物；Vite 代理只用于开发，生产反向代理和部署留到后续里程碑。Day 1 不引入自动化测试框架或 CI，验收证据见开发日志。

## 当前目录与依赖用途

- `apps/api/`：FastAPI 定义 HTTP 路由，Pydantic 约束健康响应，Uvicorn 提供 ASGI 服务，Ruff 做静态和格式检查。
- `apps/web/`：React 渲染页面状态，TypeScript 检查类型，Vite 提供开发服务器/代理和构建，Prettier 检查格式。
- `doc/planning/`：仓库原有的项目总计划、56 天计划和协作手册（实际目录为单数 `doc`，保留原样）。
- `docs/scope.md`：目标、3 条用户故事、5 个不做项及约束。
- `docs/devlog/`：按真实日期记录实现、验证、限制和下一步。

范围：[产品与项目约束](docs/scope.md) · 进度：[Day 1 开发日志](docs/devlog/2026-10-22-day-01.md) · 计划：[56 天每日计划](doc/planning/SupportOps-Agent-56天每日计划.md)。

版本兼容参考：[Vite 环境要求](https://vite.dev/guide/)、[FastAPI 最小应用](https://fastapi.tiangolo.com/tutorial/first-steps/)。安装版本以锁定文件和本次实际检查结果为准。

## Day 2 离线数据

合成数据见 [数据说明](data/demo/README.md)，含2个租户、8篇文档、10条历史工单和12条开发案例。

运行 `.\.venv\Scripts\python.exe scripts/validate_demo.py` 检查结构与关联。开发集不是冻结测试集；当前未运行模型或检索评测。

[Day 2 报告](docs/devlog/2026-11-01-day-02.md) · [故障检查原始结果](reports/day-02-data-validation.json)。

## Day 3 模型边界

从根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts/model_smoke.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
# 显式调用一次真实API；默认最多32输出token，无自动重试
.\.venv\Scripts\python.exe scripts/model_smoke.py --live
```

服务端从项目根目录 `.env.local` 加载 `OPENAI_API_KEY`（已有进程环境优先），模型默认 `gpt-4.1-mini`，可通过进程环境或 `.env.local` 中的 `OPENAI_MODEL` 覆盖。`.env.example` 只提供空值模板，真实密钥不能进入版本管理或 `VITE_*`。

provider使用Responses API、20秒总超时、`store=False`和SDK `max_retries=0`。接口统一文本、函数调用参数、usage与请求ID；mock明确标记，usage为空，不伪造token统计。当前只支持文本消息输入与函数调用输出，尚未实现工具结果回填、Agent loop、流式或自动重试。调用者使用完live provider需调用`await provider.close()`。

错误仅暴露分类、可重试标记和请求ID，不输出原始请求、响应错误正文或密钥。`quota_exhausted`不重试，临时`rate_limit`可供后续策略决定是否重试。错误标记并不代表今天已实现重试。

本次真实调用被余额耗尽阻塞；先检查[API账单](https://platform.openai.com/settings/organization/billing)，恢复额度后再手动运行一次live命令。ChatGPT订阅与API账单分别管理。当前模型为固定的连通实验选择，没有模型质量或成本对比结论。

[Day 3 报告](docs/devlog/2026-11-01-day-03.md) · [真实请求安全元数据](reports/day-03-live.json)。
