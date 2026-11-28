# SupportOps Agent

为虚构 SaaS CloudDesk 提供有据可查的技术支持：从 API 429 排障出发，逐步实现文档引用、工具查询和人工批准后的工单创建。

**Day28 中期演示**覆盖引用回答、批准建单和拒绝草稿。浏览器、HTTP 和 PostgreSQL 真实运行，生成使用确定性 mock。现有 OpenAI 凭据的真实冒烟返回 401，这项验证已延期。

首次浏览仓库可以先看 [两分钟演示](reports/day-28-demo.webm)，再读 [中期评审](docs/day-28-midpoint-review.md) 和下面的 Compose 启动步骤。

## 用 Compose 运行本机演示（Day 27）

使用已启动的 Docker Desktop 或 Docker Engine + Compose。新目录只需已跟踪的项目文件，镜像安装锁定的依赖，API 启动时迁移数据库并导入公共合成文档。

```powershell
docker compose up --build --wait --wait-timeout 120
```

打开 [本机演示](http://127.0.0.1:5173/) 并以 `user-alpha` / `local-alpha-demo` 登录。这是 Compose 专用的公开演示口令。仅网页发布到回环地址，API 和数据库留在 Compose 网络内。若 5173 已被占用，启动前设置环境变量 `SUPPORTOPS_DEMO_PORT`，例如 `$env:SUPPORTOPS_DEMO_PORT = '5274'`。

- 引用回答：开启「使用文档引用演示」，选 2.0，问「starter配额是多少？」，打开引用并刷新原文。
- 批准建单：新建排查，开启工单草稿，提交 `ws-alpha 429`，批准并打开工单详情。
- 拒绝草稿：另外提交一份工单草稿，拒绝后刷新，状态保持「已拒绝」，没有工单入口。

```powershell
# 可重复导入同一公共语料，使用 mock embedding
docker compose exec -T api python -m scripts.seed_demo
# 自动化浏览器复现，先启动上述 Compose
npm --prefix apps/web ci
npm --prefix apps/web exec -- playwright install chromium
npm --prefix apps/web run test:e2e
```

自定义端口时给浏览器测试设置 `SUPPORTOPS_E2E_URL`，如 `http://127.0.0.1:5274`。停止用 `docker compose down`，它保留演示数据卷。单独的真实模型冒烟沿用 `python scripts/model_smoke.py --live`，使用已授权的本地 OpenAI 凭据并可能计费。Compose、浏览器测试和 CI 使用确定性 mock。详见 [Day27 开发日志](docs/devlog/2026-11-19-day-27.md)。

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


## Day 13 文档引用演示

启动本地 PostgreSQL，完成 Day 8 公共文档导入及 Day 9 mock 入库，再按快速启动运行 API 和网页。勾选「使用文档引用演示」，选择产品版本 2.0，输入以下任一固定问题。展开答案下的来源，可查看原文、版本和行号。

- starter配额是多少？
- 升级到2.0会重置额度吗？
- 401能用429的办法处理吗？
- reason=minute_rate_limit且Retry-After=20，怎么办？
- reason=monthly_quota_exhausted，持续重试有用吗？

产品版本为空时会追问。证据不足时会提示无法给出确定结论。当前采用本地固定问题与摘录式回答。 [Day 13 报告](docs/devlog/2026-11-10-day-13.md)


## Day 14 RAG 复盘

25 条开发集包括原 12 条、Day 11 精确词项 3 条和新增 10 条。完成 Day 9 mock 入库后运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/search_documents.py eval --cases evals/day-14-rag.jsonl --compare
```

21 条参与证据 Hit@5 计分，mock 向量 7/21、BM25 21/21、融合 18/21。失败原因、切块依据和可复现的未解决案例见 [Day 14 技术复盘](docs/devlog/2026-11-10-day-14.md)。

## Day 15 显式状态机

排障和文档问答共用 LangGraph 编排。运行结束后可在 `GET /runs/{id}` 查看 `state_path`。原生循环入口 `python scripts/agent_smoke.py` 保留供对照。流程、验收与复现方法见 [Day 15 报告](docs/devlog/2026-11-11-day-15.md)。


## Day 16 检查点与重启恢复

在项目根目录安装锁定依赖、配置本地数据库后，启用持久模式：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m alembic upgrade head
$env:SUPPORTOPS_PERSIST_RUNS = '1'
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

持久模式用已有 PostgreSQL 保存运行归属和图检查点。重启后用原 ID 请求 `GET /runs/{id}` 加载记录，向 `POST /runs/{id}/resume` 发送空请求恢复待完成运行。终态再次恢复返回已有结果。该段固定身份属于 Day 16 历史实现；Day 22 起 API 身份来自服务端认证会话。

可信操作员 CLI 为 `python scripts/checkpoint_run.py create`、`resume <run-id>` 和 `show <run-id>`。CLI 固定使用合成只读 429 场景。进程退出后数据库记录可能仍显示 running，需要显式请求恢复。默认内存模式沿用此前的行为。故障注入、权限验证、状态保存内容与重跑边界见 [Day 16 报告](docs/devlog/2026-11-11-day-16.md)。


## Day 36 持久化 worker

`docker compose up --build` 启动 API、PostgreSQL 和一个独立 worker。API 迁移数据库并导入演示资料，worker 在 API 就绪后开始领取任务。

手工启动时，先用 `python -m alembic upgrade head` 更新同一个 PostgreSQL。启用 `SUPPORTOPS_PERSIST_RUNS=1` 启动 API，并在另一终端从项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m apps.api.worker
```

持久模式下，`POST /runs` 成功返回 202 和原始运行 ID，任务已写入数据库。用 `GET /runs/{id}` 查看 queued、running、awaiting_approval 或终态。浏览器断开与 API 重启均不消除任务。worker 重启后会继续未完成任务，需等待旧租约到期。旧版迁移前遗留的未完成运行可由所有者用原 ID 显式恢复。

运行中的 SSE 来自数据库运行快照，检查点恢复可能使快照回退。收到 `history_changed` 时可重新查询该运行。详见 [Day36 实施与验收](docs/devlog/2026-11-26-day-36.md)。

## Day 37 并发、积压与取消

持久模式在 PostgreSQL 事务中检查容量。默认允许每账号 4 个未结任务、全局 64 个未结任务，包含正在执行和等待重试的任务。待审批阶段释放容量，批准或拒绝时重新申请容量。单账号超限返回 429，全局超限返回 503。

可在 Compose 启动环境或对应进程中设定 `SUPPORTOPS_USER_RUN_LIMIT`、`SUPPORTOPS_QUEUE_LIMIT`。同一个 worker 进程默认同时执行 2 个任务，可用 `SUPPORTOPS_WORKER_CONCURRENCY` 调到 1–4；`SUPPORTOPS_RUN_TIMEOUT_SECONDS` 默认 120 秒，从本轮首次领取开始，租约重领不延长。

运行所有者可发送 `POST /runs/{id}/cancel`，然后继续查询原 ID。排队任务立即进入 `cancelled`。执行中的任务在下一次模型、工具或写入边界停止，已开始的当前操作可结束。超时记录为 `failed`，原因是 `run_timeout`。已提交的工单继续可查。[实施与验证](docs/devlog/2026-11-27-day-37.md)

## Day 38 SSE 重连与事件恢复

持久模式在同一 PostgreSQL 事务内写入运行快照和该运行的递增序号。连接中断后，浏览器用 `Last-Event-ID` 重连。刷新从原运行 ID 获取当前快照，订阅和刷新均不重新提交运行。

重放历史保留 24 小时，由 worker 分批清理。过期或缺失游标从当前权威快照恢复。最新终态可按同一序号回送以结束连接，运行详情继续保留。[实施与验证](docs/devlog/2026-11-28-day-38.md)

## Day 17 工单与待审核草稿

先执行数据库迁移并按 Day 16 说明启用持久模式。页面勾选待审核工单草稿，可查看固定 ws-alpha 演示的目标租户、工作区、动作、标题、描述和 pending 状态。API 请求为 `POST /runs`，传入 `question` 和 `ticket_draft: true`，再 GET 原运行 ID 获取 `proposed_action`。每个运行的重复相同提议复用原草稿。

`get_ticket` 是按获授权租户读取的模型工具。`POST /tickets` 是服务端受控执行接口，只接受 `approval_id`，pending 草稿返回 409。批准、拒绝及有效期与参数摘要绑定由 Day 18 实现。本日 Agent 调用 `propose_ticket` 只保存草稿，模型工具列表包含 get_ticket 与 propose_ticket。测试证据和边界见 [Day 17 报告](docs/devlog/2026-11-12-day-17.md)。


## Day 18 批准、拒绝与恢复

执行数据库迁移后，在持久模式生成新草稿。网页显示精确参数与北京时间到期时间，用户可批准并创建工单或拒绝。生成仍使用 mock。新审批默认有效 30 分钟。

API 向 `POST /approvals/{approval_id}/approve` 或 `/reject` 提交原 `run_id` 与 `payload_digest`，服务端保存决定并恢复原运行。GET 原 run 可查看最终工单或拒绝结果。决定已保存但恢复中断时，可重复发送相同决定或调用原 run 的恢复接口。

审批与用户、目标租户、run 和精确动作参数绑定，批准和建单时都校验摘要与期限。改动参数需要提交新 run 生成新审批。历史缺少绑定信息的草稿保留可读，通过新运行重新申请。完整实测与边界见 [Day 18 报告](docs/devlog/2026-11-12-day-18.md)。


## Day 19 建单幂等与执行回执

先执行 `python -m alembic upgrade head`。新草稿的 proposed_action 包含 `idempotency_key`，格式为 `create_ticket:<approval UUID>`。同一审批对应一个逻辑动作。

`POST /tickets` 接收 `approval_id`，可附带 `idempotency_key` 和 `payload_digest`。服务器校验归属、动作 key 与参数。工单、审批状态和执行回执在同一事务提交，重复调用返回保存的结果。客户端响应丢失时使用原审批与 key 重试。工单当前状态可通过 get_ticket 读取，建单回执保存执行时快照。

首次执行后 10 次顺序重试、8 路并发、响应丢失后的新进程重试、事务中途失败及历史数据回填均已在 PostgreSQL 验证，详见 [Day 19 报告](docs/devlog/2026-11-13-day-19.md)。


## Day 20 重试、预算与停止日志

运行层默认最多尝试 3 次，退避为 0.1、0.2 秒。临时错误须同时满足分类与可重试标记，权限、参数及额度耗尽直接停止。写入重试复用同一审批、动作 key 与摘要。

默认预算为 6 个逻辑决策步、8 次模型尝试、8 次工具尝试及最多 3 次写入尝试。失败尝试也计入调用次数，旧检查点恢复时沿用兼容计数。GET 原 run 可查看 steps、model_calls、tool_calls、write_calls，以及 events 中的错误、retry.scheduled 和 run.stopped。网页展示退避与停止原因。

80 项离线测试、39 项 PostgreSQL 集成测试及前端和浏览器检查通过。模型验证使用 mock 故障注入。线程等待超时后的写入结果通过原 key 核对，节点中途退出的重放边界见 [Day 20 报告](docs/devlog/2026-11-15-day-20.md)。

## Day 21 故障演示与 v0.3-actions

直接打开 [九步实测回放](reports/day-21-demo.html)：提案、未批拒绝、批准、重复批准与执行，以及实际子进程终止后的显式恢复。回放包含真实 PostgreSQL 状态与 ASGI API 返回值，生成模型为 mock；这是已录观察的离线播放，不是业务页面录屏或真实模型验收。

[实施与复现](docs/devlog/2026-11-16-day-21.md) · [事务与恢复 ADR](docs/adr/0001-actions-and-recovery.md) · [原始录制](reports/day-21-demo.json) · [回归结果](reports/day-21-validation.json)。不增加 Day 22 的认证功能。


## Day 22 种子账号与最小会话认证

原有两个合成租户提供四个账号：user-alpha / support-alpha 属于 tenant-alpha，user-beta / support-beta 属于 tenant-beta。member 读取本租户工单；support 仅分配到自己的种子租户。两种角色都只能批准自己请求的动作，不能审批他人的运行。

先在启动后端的同一 PowerShell 终端设置四个互不相同、至少 12 字符的口令。以下交互不会把口令打印到命令行；不把实际值提交到 Git：

```powershell
$seedPasswords = @{}
foreach ($seedUser in @('user-alpha','support-alpha','user-beta','support-beta')) {
    $seedPasswords[$seedUser] = Read-Host -Prompt "$seedUser 的本机口令（至少12字符）" -MaskInput
}
$env:SUPPORTOPS_DEMO_PASSWORDS = ConvertTo-Json -InputObject $seedPasswords -Compress
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

也可在忽略的 .env.local 中配置同名 JSON 字典，后端启动时读取；不要使用 VITE_*。未配置时登录返回 503，业务接口仍拒绝匿名访问。缺少账号、重复口令或不合规口令会让启动失败，错误不回显口令。

POST /session 使用 username/password 登录，GET /session 查看当前主体，DELETE /session 退出。会话令牌只保存在 HttpOnly、SameSite=Strict Cookie 中，服务端只存令牌 SHA256 摘要；有效一小时，成功重登轮换当前令牌，退出立即撤销。默认 Secure 关闭只适用于本机 HTTP；HTTPS 环境须设置 SUPPORTOPS_COOKIE_SECURE=1。本日不公开部署，不提供注册、密码重置、访客会话或多 worker 会话共享。

运行、恢复、审批详情与决定、建单和工单详情均要求会话。GET /tickets/{id} 按租户读取；GET /approvals/{id} 只返回本人关联的提案。引用通过本人 run 返回，没有任意文件读取接口。更换账号会清空页面中的旧运行；401 会返回登录页。API 不接受请求体或 X-User-ID / X-Tenant-ID 中自报身份。

会话在进程重启后失效，需要重登；PostgreSQL 运行仍可在身份完全一致时恢复。测试见 [Day 22 报告](docs/devlog/2026-11-17-day-22.md)及[实测结果](reports/day-22-validation.json)。早期各 Day 的固定身份与无登录说明仅为历史记录。Day 21 演示脚本现在显式签发可信测试身份，不改写原 Day 21 已保存证据。


## Day 23 结构化实时反馈

登录后提交运行，页面通过同源 `GET /runs/{id}/events` 接收 SSE，替换原先的自动轮询。运行中可看到步骤与工具开始、完成、重试和停止信息；终态或待审批后主动关闭连接。批准或显式重新查询会建立新的订阅。SSE 接口与原运行详情一样校验会话和资源归属。

`run.snapshot` 提供当前授权快照；`run.started`、`step.started`、`tool.started`、`tool.finished` 等报告行为；`approval.required` 表示等待审批；`run.finished` 提供完成或失败的结果。`answer.delta` 当前分发已生成的完整答案，不是模型逐 token 流，不展示模型内部思维链。页面区分运行状态和连接状态：连接失败只停止实时反馈，后台任务可能继续。

本日不实现自动重连或游标恢复，`Last-Event-ID` 请求返回 409。连接错误后由用户点击“重新查询当前运行”；序号仅在当前连接有效。每个主体最多 4 条连接，服务端读取循环设 180 秒截止，前端也保留三分钟等待保护，闲置时有注释心跳。持久模式复用当前运行读取，未测生产规模吞吐。

[Day 23 实施、事件协议与边界](docs/devlog/2026-11-17-day-23.md) · [实测输出](reports/day-23-validation.json)。运行 `npm --prefix apps/web test` 可执行 Node 内置 reducer 测试，现有 CI 已接入；没有新增测试框架。


## Day 24 历史运行与详情

登录后通过“历史运行”查看本人最近20条记录，点击问题进入运行详情；`#run/{id}`、`#run/{id}/citation/{chunk_id}` 和 `#ticket/{id}` 可在刷新后保持位置。URL 只是资源 ID，仍由服务端校验会话与归属。导航或账号切换会卸载旧视图，避免旧请求覆盖新详情。

工单草稿明确显示租户、工作区、动作、标题、内容和期限。批准或拒绝请求等待时两个按钮都禁用；批准后显示工单 ID，点击“查看工单当前详情”读取当前工单状态。拒绝不创建工单。审批失败后“重新查询当前运行”会实际读取最新详情，过期审批需新建运行申请。

引用侧栏读取的是本次运行已保存并核验的引用，显示版本、修订、行号和原文。来源路径为纯文本；API 只接受运行 UUID 与引用 UUID，不接受文件路径。错误或越权引用返回404，不回退为任意文件读取。

`GET /runs?limit=20` 返回本人摘要，最大50条；`GET /runs/{id}/citations/{chunk_id}` 返回本次运行的单条引用。新 HTTP 运行保存真实创建时间；旧记录和缺少时间的操作员 CLI 记录不补造时间。内存模式只保留当前服务中的历史，持久历史需启用原有 PostgreSQL 模式。没有分页、搜索、全量导出或新增路由框架。

[Day 24 实施与复现](docs/devlog/2026-11-18-day-24.md) · [实测输出与浏览器夹具](reports/day-24-validation.json)。本日模型和 embedding 使用 mock，浏览器与数据库真实验证，未调用付费 API。


## Day 25 独立只读 MCP

新增 `apps/mcp/server.py` 与官方 SDK 客户端，仅暴露原有 `get_workspace_usage`、`get_recent_errors`。使用真实子进程 stdio 协议发现和调用，复用原工具权限与严格参数，不是普通 HTTP 接口改名。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m apps.mcp.client --subject user-alpha
.\.venv\Scripts\python.exe -m apps.mcp.client --subject user-beta
```

客户端托管固定 server 进程，无需网页、数据库、模型密钥或付费 API。主体名称由本机受信任启动器选择，不是认证凭据；MCP 参数与请求元数据不能改变该进程身份。网页 Agent 仍走原直接工具路径，Day26 再接入 adapter 与权限传播。没有远程 HTTP/OAuth、写工具或任意第三方 server。

SDK 锁定为 mcp==2.3.0；本轮实测协议2026-07-28。新增必需依赖，既有版本不变；Windows 专用包带平台标记。[使用与架构说明](docs/mcp.md) · [Day25 日志](docs/devlog/2026-11-18-day-25.md) · [真实协议及回归输出](reports/day-25-validation.json)。
