# SupportOps Agent

为虚构 SaaS CloudDesk 提供有据可查的技术支持：从 API 429 排障出发，逐步实现文档引用、工具查询和人工批准后的工单创建。

**当前进度为 Day 12。已实现可选本地重排和预算受限的引用上下文。真实语义基线待验收。** 已有 FastAPI 健康接口和展示运行步骤的 React 页面。CloudDesk 是合成演示场景，未接入商业生产系统；已有独立模型适配与mock；已有原生Agent loop；已有独立的PostgreSQL文档存储，尚无RAG或建单能力；mock运行记录仍在内存。健康检查和mock无需密钥；live模式会使用API计费。

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

打开 [前端页面](http://127.0.0.1:5173)，应显示“429 排障演示”表单和“Day 6 · 本地 mock”标记。点击“开始排查”，应看到运行完成、两个工具步骤和明确标注的 mock 答案。[后端健康接口](http://127.0.0.1:8000/health) 返回：

```json
{"status":"ok","service":"supportops-api"}
```

接口文档：[Swagger UI](http://127.0.0.1:8000/docs)。终端按 Ctrl+C 停止对应服务。

## 请求如何到达后端

浏览器加载 React → POST 同源 `/api/runs` → Vite 去掉 `/api` 并代理到后端 `/runs` → 返回202与运行ID → 页面轮询 `/api/runs/{id}`，展示步骤和结果。页面不再自动请求健康接口；可手动访问上方健康链接验证进程与代理。

每个页面请求最多等待8秒；轮询约3分钟停止等待，可重新查询同一运行。后端 `/health` 仅表示API进程存活，不证明模型可用。未增加跨域通配符配置。

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

故障演示：后端不可用时点击“开始排查”，页面应显示请求失败；恢复后端后可以再次提交。若已有运行且轮询失败，点击“重新查询当前运行”只查询原ID。后端重启后内存记录丢失，原ID返回404。端口占用时先检查已有进程；Vite配置strictPort，不会悄悄切换端口。

`npm run build` 仅验证可生成前端产物；Vite 代理只用于开发，生产反向代理和部署留到后续里程碑。Day 1 验收证据见开发日志；Day 6已补充离线测试门禁与CI配置。

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

provider使用Responses API、20秒总超时、`store=False`和SDK `max_retries=0`。接口统一文本、函数调用参数、usage与请求ID；mock明确标记，usage为空，不伪造token统计。当前支持文本输入、函数调用及Day 5加入的结果回填与原生loop；尚无流式或自动重试。调用者使用完live provider需调用`await provider.close()`。

错误仅暴露分类、可重试标记和请求ID，不输出原始请求、响应错误正文或密钥。`quota_exhausted`不重试，临时`rate_limit`可供后续策略决定是否重试。错误标记并不代表今天已实现重试。

本次真实调用被余额耗尽阻塞；先检查[API账单](https://platform.openai.com/settings/organization/billing)，恢复额度后再手动运行一次live命令。ChatGPT订阅与API账单分别管理。当前模型为固定的连通实验选择，没有模型质量或成本对比结论。

[Day 3 报告](docs/devlog/2026-11-01-day-03.md) · [真实请求安全元数据](reports/day-03-live.json)。

## Day 4 只读工具

```powershell
.\.venv\Scripts\python.exe scripts/tools_smoke.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`ToolExecutor.execute(name, arguments, context=...)`按注册表选择工具、严格验证参数，再检查身份与工作区授权，最后调用业务函数。`definitions()`导出兼容现有模型边界的工具定义。两个工具为`get_workspace_usage`和`get_recent_errors`；它们仅返回本地合成数据。

身份通过独立、不可变的`AuthContext`传入，不能放进模型参数。member只访问本租户；support只访问受信上下文明确分配的租户，默认没有分配。未知工作区和未授权工作区返回相同的`not_found`结果，避免透露存在性。结果使用`ok/data/error`统一结构；错误不包含原始参数、文件路径或私有数据。

当前CLI中身份是开发者显式构造的演示上下文，尚未接入HTTP登录认证。不能把请求体直接转换成AuthContext当作认证。后续接入API时，user_id、tenant_id、role与支持人员分配必须由服务端可信会话产生。工具层不提供write工具，也不执行模型loop。

[Day 4报告](docs/devlog/2026-11-02-day-04.md) · [原始检查输出](reports/day-04-validation.json)。

## Day 5 原生 Agent loop

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/agent_smoke.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

该CLI仅运行已标注mock的固定ws-alpha场景，调用真实本地只读工具，不调用模型API。预期2次模型替身调用、2次串行工具调用，返回60/60分钟用量、8000/10000月用量和Retry-After=20秒的排查结论。

`AgentLoop(provider, executor).run(question, context=trusted_context)`支持注入模型provider与可信执行上下文。上限为6次模型调用、默认8次工具调用；默认每次模型/工具操作超时20秒，单操作可配置至60秒。预算耗尽、重复调用、工具错误、模型异常和超时均返回明确状态。错误后当前策略为立即停止，不尝试自动修复或重试。

Responses适配器回填原始输出项，包含无状态请求需要保留的加密reasoning数据；工具输出以相同call_id对应function_call_output。该原始数据只用于协议延续，不展示为思维链。身份不会从工具参数读取。

[Day 5报告](docs/devlog/2026-11-02-day-05.md) · [离线检查](reports/day-05-validation.json) · [mock运行轨迹](reports/day-05-mock-run.json)。

## Day 6 本地演示与CI

按上方两个终端启动后打开 http://127.0.0.1:5173 ，点击“开始排查”。页面通过POST /api/runs提交问题，取得202与运行ID，然后每500ms查询GET /api/runs/{id}，展示状态、工具步骤和最终答案。每个HTTP请求超时8秒；轮询达到约3分钟后停止等待，可重新查询同一运行。

后端POST /runs只接受question（1至2000字符），固定服务端user-alpha身份和mock模式。已接受的任务独立于单个HTTP连接；关闭页面不会取消任务。GET /runs/{id}返回当前事件与结果，未知或被清理ID返回404。

该版本只供本机开发：单进程内存记录，最多4个活跃任务、100条记录，满载503，记录满时先清理最旧已结束记录；重启丢失。请使用单个Uvicorn worker，不要公开部署或用于多访客隔离。没有登录、持久任务队列或断线自动恢复。

离线Python门禁：`.\.venv\Scripts\python.exe -X utf8 scripts/check_offline.py`。前端门禁：`npm.cmd --prefix apps/web run format:check`、`run typecheck`、`run build`。CI使用相同命令，且不传入API密钥；依赖下载仍需网络。配置位于.github/workflows/ci.yml，推送后才能确认GitHub托管环境的实际结果。

本地30秒录屏：output/playwright/day6-demo-30s.webm（被Git忽略）。演示步骤：输入ws-alpha的429问题→开始排查→观察运行ID与四条事件→阅读分钟限流结论。故障演示与截图详见[Day 6报告](docs/devlog/2026-11-02-day-06.md)。

## Day 7 第一周复盘

[Day 7报告](docs/devlog/2026-11-04-day-07.md)记录三个踩坑、版本边界和口述提纲。`v0.1-loop`是本地mock里程碑；真实模型成功验收、远端CI结果及用户亲自口述仍分别保留状态。

## Day 8 文档入库与版本

新增独立PostgreSQL文档存储、Alembic迁移及可信本机操作员Markdown CLI；应用启动和mock排障不依赖数据库。先准备PostgreSQL+pgvector并在忽略的`.env.database.local`设置DATABASE_URL，再执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -X utf8 scripts/import_documents.py import --scope public --product-version 1.0 --product-version 2.0
.\.venv\Scripts\python.exe -X utf8 scripts/import_documents.py list --scope public
.\.venv\Scripts\python.exe scripts/documents_smoke.py
```

相同文档与修订元数据复用版本，内容或元数据变化追加版本；公共文档与租户私有文档分范围保存。chunks今日仅建表，不切块、不生成embedding、不创建向量索引。全部8篇现有公共规则已实际导入，本机正常重启数据库后摘要一致。

[数据库准备、表结构、版本与删除策略](docs/database.md) · [Day 8报告](docs/devlog/2026-11-05-day-08.md) · [实际检查记录](reports/day-08-validation.json)。独立数据库测试命令：`python -m unittest discover -s tests/integration -v`，必须显式设置专用TEST_DATABASE_URL（库名以_test结尾）；默认离线门禁不连接数据库，不用SQLite模拟PostgreSQL验收。

## Day 9 切块与 embedding

新增800字符上限、长段落100字符重叠的确定性标题/段落切块，保留标题路径与原文位置；持久化1536维embedding缓存、单版本处理锁、原子完成状态与失败恢复。网页仍mock，没有向量检索或RAG回答。

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
# 离线预览，不读数据库、不调用API
.\.venv\Scripts\python.exe -X utf8 scripts/process_documents.py preview
# 默认mock，仍需PostgreSQL；向量只用于工程验证，无语义意义
.\.venv\Scripts\python.exe -X utf8 scripts/process_documents.py embed
# 显式付费调用，只处理第一篇可见公共文档
.\.venv\Scripts\python.exe -X utf8 scripts/process_documents.py embed --live --limit 1
```

真实模式固定OpenAI text-embedding-3-small/1536维；每批最多16条、每个请求20秒，最多3次尝试，仅临时错误重试；余额耗尽立即失败。当前实际小请求返回quota_exhausted，未写入真实向量。更多边界见[Day 9说明](docs/ingestion.md)、[报告](docs/devlog/2026-11-06-day-09.md)、[切块审阅材料](reports/day-09-chunk-review.md)。


## Day 10 向量检索与评测

完成 Day 8 导入和 Day 9 mock 入库后，从项目根目录运行以下命令。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/search_documents.py search "starter配额是多少？" --tenant-id tenant-alpha --product-version 2.0 --k 5
.\.venv\Scripts\python.exe -X utf8 scripts/search_documents.py eval
```

查询要求显式产品版本。省略租户时只查公共资料，指定演示租户时可见公共资料和该租户资料。结果包含 chunk ID、正文、来源、修订与位置，以及余弦相似度。CLI 使用 Day 9 mock provider。

开发集 12 例，计分 10 例，mock Hit@5 实测 4/10。逐例结果与语料修订见 [mock 评测记录](reports/day-10-retrieval-mock.json)。真实语义基线状态和复现细节见 [Day 10 报告](docs/devlog/2026-11-08-day-10.md)。


## Day 11 关键词检索与融合

完成 Day 9 mock 入库后，可用同一个 CLI 运行 BM25、融合与对照评测。省略 `--mode` 时沿用向量检索。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/search_documents.py search "401 token" --tenant-id tenant-alpha --product-version 2.0 --mode bm25
.\.venv\Scripts\python.exe -X utf8 scripts/search_documents.py search "401 token" --tenant-id tenant-alpha --product-version 2.0 --mode hybrid
.\.venv\Scripts\python.exe -X utf8 scripts/search_documents.py eval --compare
```

原开发集三路 Hit@5 分别为向量 4/10、BM25 10/10、融合 9/10。补充集分别为 1/3、3/3、2/3。向量使用 mock。逐例排名、融合漏召回和运行边界见 [Day 11 报告](docs/devlog/2026-11-09-day-11.md)。


## Day 12 重排与上下文

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/search_documents.py search "401 token" --product-version 2.0 --mode hybrid --context
.\.venv\Scripts\python.exe -X utf8 scripts/search_documents.py search "401 token" --product-version 2.0 --mode hybrid --context --rerank
.\.venv\Scripts\python.exe -X utf8 scripts/search_documents.py eval --compare-context
```

上下文模式从至多 20 个候选中选片段，默认至多 5 段及 2048 字节。CLI 可通过 `--context-budget` 调整以 UTF-8 字节计的保守预算。片段前的 ID 可在 `sources` 中查到出处和修订位置。重排默认关闭，显式启用时使用本地 BM25，故障时按原检索顺序组装。 [Day 12 报告](docs/devlog/2026-11-09-day-12.md)
