# SupportOps Agent：56 天每日执行清单

配套：[项目总计划](./SupportOps-Agent-项目总计划.md) · [面试与 AI 协作手册](./SupportOps-Agent-面试与AI协作手册.md)。

适用前提：已有 Python／后端基础，每天 3–4 小时。Day 1 是实际开始日，不要求按日历连续打卡。每周第 7 天是约 1 小时的轻量复盘，可用于休息和处理遗留问题。出现阻塞优先顺延，提交记录应反映真实工作。

## 每天固定工作法

1. 15 分钟：读前一天记录，写今天 Issue、完成标准和不做的范围。
2. 90–120 分钟：让 AI 先提出小方案，审阅后分模块实现；每次只处理一个可验收改动。
3. 40–50 分钟：亲自跑正常与反例路径，审查 diff，修复问题。
4. 25–30 分钟：学习当天面试主题，脱离 AI 用自己的话解释。
5. 10–15 分钟：提交代码，记下结果、阻塞和明天第一步。

下面的提交名称是建议的拆分方式，不是必须完成的配额。一个提交必须对应真实修改；`test` 提交应覆盖实际风险，文档只记录新结论。当天未完成的内容留在 Issue，不伪造完成状态。

## 第 1 周：跑通原生 Agent，建立工程底座

### Day 1 — 定义问题与建立仓库骨架

- **实现**：写产品的一句话目标、3 条用户故事和 5 个不做项；确定 429 排障为主场景。初始化 Python 项目、FastAPI `/health`、最小 React 页面、格式检查与 `.env.example`。只先建会用到的目录。
- **AI 协作**：让 AI 根据产品目标给出最小目录和依赖解释，再生成骨架；你删掉没有当前用途的抽象。若前端不熟，让 AI 解释页面调用 API 的完整路径。
- **验收**：后端健康检查成功；浏览器能访问前端并请求健康接口；环境变量文件不进入提交；README 写出真实启动步骤。
- **提交建议**：`docs: define support workflow and scope`；`chore: bootstrap api and web`；`chore: add lint and env example`。
- **面试主题**：为什么选择这个业务？为什么先用单体？

### Day 2 — 建立事实、数据和评测雏形

- **实现**：定义虚构产品的版本、配额、错误码和角色事实表；手写 8–10 篇短文档、10 条历史工单、两个租户的样例。制定 eval JSONL 字段：问题、租户、相关来源、预期行为、禁止动作、类别。先录入 12 个开发案例。
- **AI 协作**：让 AI 检查事实冲突并补充缺少信息、版本混淆等反例；你逐条审核答案与来源。
- **验收**：每个可回答样例有明确依据；至少 3 个无法直接回答或需要追问的例子；合成数据有标记。
- **提交建议**：`feat: add synthetic product fixtures`；`test: add initial task fixtures`；`docs: document data provenance`。
- **面试主题**：为什么评测要从第一周开始？

### Day 3 — 模型调用适配与 mock

- **实现**：定义统一请求、回答、工具调用和 usage 类型；接一个模型 provider，另做确定性 mock；处理配置缺失、超时、429 和异常输出。记录请求 ID，不打印密钥。
- **AI 协作**：让 AI 先列 provider 接口和错误类别，再实现一个适配器；本人核对所选供应商当日文档。开发时以 mock 为主。
- **验收**：mock 可离线运行；配置好个人凭据后完成一次真实调用；异常返回可解释；密钥只在服务端。真实调用需在实际开发日完成必要的账户配置。
- **提交建议**：`feat: add model provider interface`；`feat: add mock and live adapters`；`test: cover provider failures`。
- **面试主题**：temperature、上下文窗口、token 和结构化输出分别是什么？

### Day 4 — 先做工具，再接 Agent

- **实现**：为 `get_workspace_usage`、`get_recent_errors` 定义 Pydantic 参数/结果 schema；对接 Day 2 固定数据。建立工具注册表、统一结果和错误；将工作区权限检查放在执行层。
- **AI 协作**：让 AI 生成正常、未知工作区、跨租户和参数类型错误的候选用例；你确认服务端身份不是工具参数里的自报身份。
- **验收**：直接调用工具有稳定结果；越权请求不能读取数据；非法参数不会进入业务函数。
- **提交建议**：`feat: add read-only support tools`；`feat: enforce tool authorization`；`test: cover tool input boundaries`。
- **面试主题**：JSON Schema 能保证什么，不能保证什么？

### Day 5 — 手写最小 Agent loop

- **实现**：实现模型调用 → 解析工具请求 → 验证 → 执行 → 回填结果 → 继续；设置最大 6 步、每步超时和停止状态；支持多次工具调用但先串行执行。
- **AI 协作**：让 AI 给流程伪代码和失败分支，你先讲通后再让其补实现；亲自追踪一轮消息中 tool call ID 的对应关系。
- **验收**：429 场景调用正确工具后回答；不存在的工具、无效 JSON、重复无进展调用能停止；mock 测试可稳定重现。
- **提交建议**：`feat: implement bounded agent loop`；`test: cover tool errors and stop conditions`；`docs: explain agent message lifecycle`。
- **面试主题**：Agent 与一次普通 LLM 请求、固定工作流有何区别？

### Day 6 — 最小可视化与第一条 CI

- **实现**：新增 `/runs` 和简单运行详情；页面支持输入问题、展示步骤和最终回答。先用轮询显示状态，SSE 后续再做。配置格式、类型或静态检查、确定性测试的 CI。
- **AI 协作**：让 AI 搭页面和 CI；你检查失败状态是否可见、CI 是否真的在无密钥环境运行。
- **验收**：从浏览器跑完整流程；故意打破一个工具测试能让 CI 失败，修复后恢复；录制 30 秒本地演示。
- **提交建议**：`feat: expose agent runs in web ui`；`ci: add offline quality checks`；`docs: add first demo walkthrough`。
- **面试主题**：HTTP 请求生命周期与长任务生命周期有什么差别？

### Day 7 — 轻量复盘与 v0.1

- **实现**：回看一周提交；写 3 个踩坑及原因；清理一个高价值小问题；标记 `v0.1-loop`。不补新框架。
- **AI 协作**：让 AI 扮演面试官追问原生 loop；关掉代码后自己画执行过程。
- **验收**：用 3 分钟讲清工具为何被调用、失败如何结束；README 与实际启动一致。
- **提交建议**：`docs: publish week one retrospective`；确有修复时再加 `fix: ...`。
- **面试主题**：Python 可变对象、异常传播、类型提示。

## 第 2 周：做可衡量的 RAG

### Day 8 — 文档入库与版本

- **实现**：接 PostgreSQL、迁移工具和 pgvector；新增文档、版本、chunk 表；支持 Markdown 导入、内容哈希、来源路径、租户和版本元数据。PDF/OCR 暂不做。
- **AI 协作**：让 AI 提议最小表结构与迁移；本人确认唯一键和删除策略。
- **验收**：重复导入不重复创建同版本；更新内容生成新版本；重启服务仍能查询文档。
- **提交建议**：`feat: add document persistence and migrations`；`feat: import versioned markdown`；`test: cover duplicate imports`。
- **面试主题**：数据库索引、唯一约束、迁移的作用。

### Day 9 — 切块与 embedding

- **实现**：按标题与段落切块，保留位置、标题路径和版本；先确定一组长度/重叠参数；批量 embedding、失败重试、按内容哈希缓存；按所选模型锁定维度。
- **AI 协作**：让 AI 检查长表格、代码块和跨段落问答的切块效果；你人工查看至少 10 个 chunk。
- **验收**：chunk 可追溯原文；重复导入无需重算同内容向量；失败不会标记成成功入库。
- **提交建议**：`feat: add heading-aware chunking`；`feat: cache document embeddings`；`test: cover ingestion recovery`。
- **面试主题**：chunk 大小、overlap 与召回/成本的关系。

### Day 10 — 向量检索基线

- **实现**：实现 top-k 向量搜索；先按租户、版本过滤；将检索结果统一为 chunk ID、内容、来源、分数；用初始开发集跑 Hit@5，记录未命中样例。
- **AI 协作**：让 AI 写评测脚本框架；本人核对“至少命中一个”与“召回全部相关片段”是不同指标。
- **验收**：保存第一份可复现基线，包含数据版本和实际分数；跨租户不能召回；没有结果时返回空集。
- **提交建议**：`feat: add scoped vector retrieval`；`eval: record retrieval baseline`；`test: prevent cross-tenant retrieval`。
- **面试主题**：cosine、内积、向量归一化；精确与近似搜索。

### Day 11 — 关键词召回与融合

- **实现**：为小数据量添加 BM25，明确中文分词和错误码 token 的处理；与向量搜索用 RRF 融合；保持两路相同权限过滤。加入错误码、版本号、产品名精确匹配案例。
- **AI 协作**：让 AI 提议难以仅靠语义召回的问题；你比较向量、BM25、混合三组结果。
- **验收**：产出逐案例对照；没有提升也如实保留结果，不为了混合检索强行声称有效。记录内存索引对大规模数据的限制。
- **提交建议**：`feat: add keyword and fused retrieval`；`eval: compare retrieval strategies`；`docs: record retrieval tradeoffs`。
- **面试主题**：BM25、RRF 与直接相加分数的区别。

### Day 12 — Rerank 与上下文组装

- **实现**：加入可关闭的重排阶段；从候选中筛选少量片段，按 token 预算装入上下文；去重、保留来源 ID。比较启用/关闭的耗时和命中效果。
- **AI 协作**：让 AI 帮写实验配置与结果表；本人决定默认是否启用，不能只因“简历需要”保留。
- **验收**：报告有质量与延迟两列；重排故障可降级；过长输入不会无限增长。
- **提交建议**：`feat: add optional reranking`；`feat: bound retrieval context size`；`eval: measure reranking tradeoff`。
- **面试主题**：bi-encoder 与 cross-encoder 的用途和成本。

### Day 13 — 引用回答与知识不足处理

- **实现**：将 `search_docs` 注册为 Agent 工具；要求输出引用 ID；服务端检查 ID 是否属于本次上下文；页面显示原文与版本。为缺失信息实现追问，依据不足则拒绝给确定结论。
- **AI 协作**：让 AI 制造引用不存在、文档矛盾、问题无答案三类样例；你区分“引用存在”与“引用真的支持结论”。
- **验收**：5 个固定问题可点击来源；无依据的问题不会编造文档；人工检查至少 10 条回答。
- **提交建议**：`feat: add grounded answers with citations`；`test: cover missing and conflicting evidence`；`feat: display source excerpts`。
- **面试主题**：检索失败和生成失败如何分别定位？

### Day 14 — 轻量复盘与 v0.2

- **实现**：将开发样例扩充到约 25 条，汇总 3 个 RAG 失败原因，写第一篇技术复盘，发布 `v0.2-rag`。
- **AI 协作**：让 AI 根据失败案例提问，不替你编造提升百分比。
- **验收**：可以回答“为什么当前切块方式适合你的语料”；能展示一个未解决案例。
- **提交建议**：`docs: publish rag baseline and failure analysis`；`eval: expand development cases`。
- **面试主题**：SQL JOIN、事务隔离、EXPLAIN 基础。

## 第 3 周：状态、人工审批与可靠写入

### Day 15 — 迁移到显式状态机

- **实现**：画出 classify / clarify / retrieve / call_tool / answer / await_approval / execute / done 状态；用 LangGraph 实现主流程；保留原生 loop 作为教学基线，避免两套主实现长期漂移。
- **AI 协作**：让 AI 对照原生流程提出迁移步骤；你确认哪个决策交给模型、哪个由代码控制。
- **验收**：现有正常/失败用例继续通过；状态跳转有明确条件，不靠无限对话推进。
- **提交建议**：`refactor: orchestrate support flow with explicit state`；`test: preserve baseline behavior`；`docs: record orchestration decision`。
- **面试主题**：Agentic 决策与确定性工作流的分界。

### Day 16 — Checkpoint 与重启恢复

- **实现**：接持久化 checkpointer；保存 run/thread 与租户映射；实现加载已存在运行；对只读步骤做故障注入。
- **AI 协作**：让 AI 提出进程退出发生在步骤前、中、后时的差异；本人实际终止并重启进程。
- **验收**：至少一个中断流程可恢复；使用其他租户的 run ID 被拒绝；说明哪些节点恢复时可能重跑。
- **提交建议**：`feat: persist agent checkpoints`；`test: resume interrupted read workflow`；`docs: describe recovery boundaries`。
- **面试主题**：checkpoint 与业务数据库分别保存什么？

### Day 17 — 工单模型与动作草稿

- **实现**：建立 ticket 和 approval 表、`get_ticket` 工具；新增 `create_ticket` 的受控执行接口；Agent 此时只生成动作草稿，不直接写入。草稿包含精确参数与目标租户。
- **AI 协作**：让 AI 生成模型与 schema；本人审核状态枚举、必填字段和访问策略。
- **验收**：生成草稿不会创建工单；用户能看见将执行的内容；普通用户不能查其他租户工单。
- **提交建议**：`feat: add tickets and proposed actions`；`feat: scope ticket reads`；`test: keep proposals side-effect free`。
- **面试主题**：数据库状态机与 LLM 输出结构的关系。

### Day 18 — 人工批准、拒绝与参数绑定

- **实现**：新增 approve/reject 接口；将审批与用户、run、动作参数摘要、有效期绑定；批准后恢复图执行；修改参数必须生成新审批。
- **AI 协作**：让 AI 从过期、错用户、换参数、重复提交等角度找漏洞；本人确认服务端校验覆盖每条路径。
- **验收**：批准可建单，拒绝不建单；过期或内容已变化的审批无效；LLM 无法通过输出“已批准”绕过检查。
- **提交建议**：`feat: add approval-gated execution`；`test: bind approval to actor and payload`；`docs: document approval lifecycle`。
- **面试主题**：为何不能只在 prompt 中写“危险操作需要确认”？

### Day 19 — 幂等与重复请求

- **实现**：确定一个逻辑动作的 idempotency key；使用唯一约束、事务和已有结果查询防重复写入；记录工具执行结果；模拟已写入但响应丢失。
- **AI 协作**：让 AI 分析“先写状态再建单”和“先建单再写状态”的失败窗口；本人用实际约束消除当前数据库内写入的重复问题。
- **验收**：顺序重复 10 次、并发重复请求和重启后重试均只产生一条工单；相同 key 不同参数被拒绝。
- **提交建议**：`fix: make ticket creation idempotent`；`test: cover duplicate and lost-response writes`；`docs: explain delivery semantics`。
- **面试主题**：at-least-once、幂等与 exactly-once 的边界。

### Day 20 — 错误分类与预算停止

- **实现**：区分可重试超时/限流与不可重试权限/参数错误；设置指数退避和最大次数；仅对允许重试的动作执行重试；加入运行步数和模型调用预算。
- **AI 协作**：让 AI 根据不同错误设计决策表；你核对写操作重试是否依赖幂等。
- **验收**：连续失败能在有限时间内结束；权限错误不重试；日志能显示停止原因和次数。
- **提交建议**：`feat: add classified retry policy`；`feat: enforce run budgets`；`test: cover retry exhaustion`。
- **面试主题**：超时、重试、退避、熔断各解决什么问题？

### Day 21 — 轻量故障演示与 v0.3

- **实现**：录制“提案 → 批准 → 重复批准 → 只建一单”；再演示一次中断恢复；整理 ADR，发布 `v0.3-actions`。
- **AI 协作**：让 AI 根据实际录屏步骤追问副作用边界。
- **验收**：不用解释隐藏步骤，别人能看到状态变化；说清“恢复”不等于永不重复执行节点。
- **提交建议**：`docs: publish approval and recovery demo`；必要时 `fix: ...`。
- **面试主题**：事务原子性、锁、死锁的基本处理。

## 第 4 周：完整演示产品与 MCP

### Day 22 — 种子用户与权限闭环

- **实现**：把前期开发身份替换成最小会话认证；提供两个隔离种子租户和角色；在 runs、documents、tickets、approvals 上统一验证主体与资源关系。公开访客会话隔离在 Day 48 完成。
- **AI 协作**：让 AI 检查所有资源 ID 入口，列出可能漏检的路径；本人逐个验证。
- **验收**：更换 URL 中 ID 无法读取其他租户运行、引用和工单；用户角色不能任意审批他人动作。
- **提交建议**：`feat: add authenticated demo sessions`；`fix: enforce resource-level authorization`；`test: cover tenant isolation`。
- **面试主题**：认证与授权、RBAC 与资源归属。

### Day 23 — SSE 与可理解的运行状态

- **实现**：定义 run.started / step.started / tool.finished / approval.required / answer.delta / run.finished 等事件；SSE 推送；前端显示运行中、待审批、失败、完成。
- **AI 协作**：让 AI 实现事件类型与 UI reducer；本人核对先后顺序和错误事件。只展示工具行为与简短状态，不伪装展示模型内部思维链。
- **验收**：工具运行时页面有反馈；发生异常后停止加载；mock 可回放固定事件序列。断线恢复留到 Day 38。
- **提交建议**：`feat: stream structured run events`；`feat: render agent progress states`；`test: cover event ordering`。
- **面试主题**：SSE 与 WebSocket 如何选择？

### Day 24 — 引用、审批与历史详情

- **实现**：增加引用侧栏、动作参数卡片、批准/拒绝按钮、历史运行页；按钮等待响应时禁用；成功后展示工单 ID 和跳转。
- **AI 协作**：让 AI 按已有 API 做页面；你检查用户是否知道批准什么、为什么失败、下一步能做什么。
- **验收**：不查看后台日志也能完成主流程；刷新后可查历史；错误引用 ID 不会访问任意文件路径。
- **提交建议**：`feat: add approval and citation panels`；`feat: add run history details`；`fix: clarify failed action states`。
- **面试主题**：前端幂等防抖为什么不能代替后端幂等？

### Day 25 — 独立 MCP Server（进度正常时）

- **实现**：把两个现有只读工具通过独立 MCP Server 暴露；首版采用本地受控传输；写最小客户端验证工具发现、调用与错误返回。工具业务实现复用已有权限层。
- **AI 协作**：让 AI 对照当前官方 SDK 文档实现协议接入；本人区分 MCP tool schema 与模型 API tool schema。
- **验收**：通过真实 MCP 客户端发现并调用工具；不能把普通 HTTP API 改名当作 MCP。
- **提交建议**：`feat: expose read tools through mcp`；`test: add mcp contract checks`；`docs: describe host client server roles`。
- **面试主题**：MCP 解决什么连接问题？不能代替哪些业务能力？
- **裁剪**：若主线未完成，这一天修主线，将 MCP 延后并标为未实现。

### Day 26 — MCP 接入与权限传播（进度正常时）

- **实现**：Agent 通过 MCP adapter 调用只读工具；身份来自受控执行上下文而非模型；限制可接入 server 与工具名单；给 MCP 路径加入 timeout 和结构化错误。
- **AI 协作**：让 AI 审核权限是否在跨进程后丢失；本人记录当前本地传输的部署限制，远程 OAuth 不纳入本轮。
- **验收**：主流程可切换直接/MCP 两种只读路径；超时不会挂死；跨租户仍被拒绝。
- **提交建议**：`feat: route read tools through mcp adapter`；`test: preserve auth across tool transport`；`docs: record mcp limitations`。
- **面试主题**：工具描述不可信时，权限应在哪一层执行？

### Day 27 — 第一套端到端验收

- **实现**：加入 3 条 E2E：引用回答、批准建单、拒绝/失败；使用 mock 保证 CI 可复现；另跑少量真实模型冒烟测试。完成本地 Compose 和 seed 命令。
- **AI 协作**：让 AI 生成浏览器测试骨架；本人确认测试验证的是结果，不是只检测按钮存在。
- **验收**：新目录按 README 启动后，三条流程可复现；真实模式和 mock 模式标记清楚。
- **提交建议**：`test: cover core browser workflows`；`chore: add compose and seed workflow`；`docs: update reproducible demo steps`。
- **面试主题**：单元、集成、E2E、模型评测怎样分工？

### Day 28 — 轻量中期评审与 v0.4

- **实现**：录制 2 分钟完整演示；列出剩余风险、最有价值的 3 项工作和可砍功能；发布 `v0.4-demo`。开发样例达到约 40–50 条。
- **AI 协作**：让 AI 用“招聘方打开仓库 3 分钟”的视角检查可理解性。
- **验收**：能完成完整业务闭环；若不能，第 5 周前两天先修主线，缩小评测规模。
- **提交建议**：`docs: publish midpoint demo and scope review`；`eval: add workflow development cases`。
- **面试主题**：现场 5 分钟项目介绍。

## 第 5 周：评测、安全边界与失败分析

### Day 29 — 120 条样例与分组冻结

- **实现**：将样例整理为问答 40、工具 30、流程 20、注入 15、权限/审批 15；按来源问题簇分成 60 个开发与 60 个最终测试案例；保存分组脚本、版本和内容哈希。
- **AI 协作**：AI 扩充候选反例，你核对标签、来源与预期；禁止用最终测试结果来选择 prompt。数据准备时间超出一天时优先保证标注质量并缩小规模。
- **验收**：结构校验通过；近重复问题不跨分区；对无答案场景定义可接受追问/拒答。
- **提交建议**：`eval: curate task and adversarial datasets`；`eval: freeze grouped test split`；`docs: add dataset card`。
- **面试主题**：数据泄漏、过拟合、小样本评测的限制。

### Day 30 — 确定性评分器

- **实现**：对工具选择、schema、参数约束、步骤上限、写入数量、最终工单状态写评分器；检索指标单独计算；输出每个 case 的结果与失败理由。
- **AI 协作**：让 AI 写评分函数草稿；你故意构造错误输出验证评分器真的能发现错误。
- **验收**：不是“HTTP 200 就算成功”；一个回答看似正确但没有实际建单的案例应判失败。
- **提交建议**：`eval: add tool and state outcome scorers`；`test: validate scorer failure detection`；`eval: export per-case results`。
- **面试主题**：过程正确与结果正确分别如何测量？

### Day 31 — 答案质量与人工校准

- **实现**：制定正确性、依据性、引用支持、拒答合理性评分规则；人工审阅至少 20 条开发集输出；可加 LLM judge，比对其与人工的分歧。
- **AI 协作**：AI 可做辅助评分，但不能同时生成问题、答案再无审查地宣布正确；你逐条解释 5 个分歧案例。
- **验收**：保存评分 rubric、人工标签与判定理由；judge 模型/版本单独记录；不把 judge 高分直接当事实。
- **提交建议**：`eval: add answer quality rubric`；`eval: calibrate judge against human review`；`docs: analyze grading disagreements`。
- **面试主题**：LLM-as-a-judge 的偏差和校准方法。

### Day 32 — Prompt injection 与越权反例

- **实现**：添加恶意文档指令、工具输出注入、伪造“用户已批准”、跨租户引用和过期审批案例；明确资料只是数据；检查工具参数和调用权限的执行边界。
- **AI 协作**：让 AI 提出攻击候选；本人确认每一条预期是“不能产生什么动作”，而非只看回答是否礼貌拒绝。
- **验收**：当前固定集合中没有越权读取/写入；失败就先修权限；报告范围只限定于本次测试集。
- **提交建议**：`test: add adversarial agent scenarios`；`fix: enforce execution trust boundaries`；`docs: report security test scope`。
- **面试主题**：prompt 防御与权限隔离各有什么作用？

### Day 33 — 上下文与记忆

- **实现**：区分原始对话、短摘要和结构化事实；给摘要设置 token 预算，保留关键事实来源；长期偏好只存显式确认内容，并提供删除；按用户/租户隔离。
- **AI 协作**：让 AI 创建长对话中用户更正版本、撤回信息的样例；本人检查摘要是否将过期事实继续当真。
- **验收**：15 轮模拟对话不无限膨胀；用户更正后使用新值；其他用户无法读到记忆。时间不足只做短期摘要，长期记忆标为后续。
- **提交建议**：`feat: manage bounded conversation context`；`test: preserve corrected facts and isolation`；`docs: record memory policy`。
- **面试主题**：上下文、缓存、记忆、知识库分别是什么？

### Day 34 — 开发集质量报告

- **实现**：一条命令运行 60 条开发样例；导出 JSON 与 Markdown/HTML；记录 SHA、数据/模型/prompt 版本、成功率、失败类别、耗时、usage。挑 5 个失败做归因。
- **AI 协作**：让 AI 帮你整理报告，不改写失败成成功；本人区分检索、模型、工具、基础设施故障。
- **验收**：报告可追溯每个结果；脚本可重跑；至少提出两个基于证据的修复方向。
- **提交建议**：`eval: add reproducible batch runner`；`docs: publish development evaluation report`；`fix: address highest-impact failure`。
- **面试主题**：平均分掩盖哪些问题？为什么要看失败分布？

### Day 35 — 轻量回归门禁

- **实现**：把确定性评分器、权限、幂等测试纳入 CI；真实模型评测作为手动工作流并设预算；写第二篇技术复盘。
- **AI 协作**：让 AI 检查测试是否依赖真实模型或未配置密钥；本人确认 PR 不会意外产生大量调用费用。
- **验收**：故意删除一次权限检查能让对应测试失败；修复后通过；发布 `v0.5-eval`。
- **提交建议**：`ci: gate deterministic agent regressions`；`docs: publish evaluation lessons`。
- **面试主题**：哪些检查每次 PR 跑，哪些在发布前跑？

## 第 6 周：异步任务、可观测性与性能

### Day 36 — 持久化任务 worker

- **实现**：将 `/runs` 改为提交任务后尽快返回；增加任务领取、lease、心跳、重试次数和 worker 进程；可用数据库行锁安全领取，先运行单 worker。
- **AI 协作**：让 AI 画领取到完成的状态图；本人检查进程崩溃后 lease 到期如何恢复。
- **验收**：HTTP 连接结束不导致已提交任务丢失；worker 重启后任务可继续；既有写入幂等测试仍通过。
- **提交建议**：`feat: execute runs with persistent worker`；`test: recover expired task leases`；`docs: record queue design tradeoffs`。
- **面试主题**：为什么后台任务不能只依赖进程内 fire-and-forget？

### Day 37 — 有限并发、取消与积压

- **实现**：设置 worker 并发、每用户进行中任务数和队列上限；新增取消状态；在步骤边界检查取消；运行超时有明确最终状态。取消不会自动撤销已提交业务写入。
- **AI 协作**：让 AI 生成取消前、工具执行中、写入后等场景；你定义每种情况下的用户承诺。
- **验收**：超过容量返回可解释提示；取消后不再启动新步骤；已发生动作会如实显示。
- **提交建议**：`feat: bound concurrency and queue admission`；`feat: support cooperative cancellation`；`test: cover cancellation boundaries`。
- **面试主题**：背压、并发上限与吞吐量的关系。

### Day 38 — SSE 重连与事件恢复

- **实现**：将 run events 持久化并分配递增序号；客户端按最后接收序号重连，去重；连接断开不重复启动任务；设事件保留期限。
- **AI 协作**：让 AI 设计断网、刷新和重复事件序列；本人在浏览器模拟网络中断。
- **验收**：刷新后恢复同一 run；重复事件不重复显示工单；终态不会退回运行中。
- **提交建议**：`feat: replay persisted run events on reconnect`；`test: handle duplicate and delayed events`；`fix: preserve terminal ui state`。
- **面试主题**：任务恢复和 UI 重连为什么是两件事？

### Day 39 — Trace 与故障定位

- **实现**：统一 request/run/step ID；记录模型、检索、工具、审批和数据库步骤耗时；日志脱敏；在运行详情中显示可理解的事件。Langfuse 接入作为可选增强。
- **AI 协作**：让 AI 根据失败日志定位故障，再由你验证因果；检查日志是否记录了不必要的输入内容。
- **验收**：给定失败 run ID，5 分钟内能找出失败步骤、错误类别和是否发生写入。
- **提交建议**：`feat: correlate traces across agent steps`；`fix: redact sensitive trace fields`；`docs: add troubleshooting runbook`。
- **面试主题**：日志、指标和 trace 各适合回答什么问题？

### Day 40 — 用量、成本与缓存

- **实现**：记录模型输入/输出 usage、估算与真实返回的区别；建立带日期币种的价格配置；成本上限触发停止后续步骤。先缓存 embedding/检索结果，key 包含租户、文档版本、模型版本。
- **AI 协作**：让 AI 检查缓存串租户和过期引用问题；本人写明确失效规则。语义答案缓存留作选修。
- **验收**：报告能区分缓存命中与未命中；价格缺失时标为未知；新文档版本不命中过期结果。
- **提交建议**：`feat: track usage and estimated run cost`；`feat: add versioned retrieval cache`；`test: invalidate stale and foreign cache entries`。
- **面试主题**：为什么还要看每个成功任务的成本？

### Day 41 — 压测与故障注入

- **实现**：mock 模式测试 API/队列在并发 1、5、10 下的表现；真实模式用小样本观察模型耗时；记录硬件、场景、p50/p95、错误率；注入工具延迟、429 和 worker 退出。
- **AI 协作**：让 AI 写负载脚本与统计；本人区分模型服务瓶颈与本地 API 瓶颈，不用 mock 成绩代表真实模型吞吐。
- **验收**：产出可复现实验与一个实际优化；小于约 100 个样本的尾延迟结果注明不稳定。
- **提交建议**：`perf: add repeatable workload benchmark`；`fix: address measured bottleneck`；`docs: publish latency and failure report`。
- **面试主题**：为什么平均延迟好看，用户仍觉得慢？

### Day 42 — 轻量可靠性复盘与 v0.6

- **实现**：检查“工作进程重启、重复提交、断线、超时、取消”五种场景；给恢复能力写明限制；发布 `v0.6-reliability`。
- **AI 协作**：让 AI 做一次故障演练提问，你用日志和代码回答。
- **验收**：能准确区分已支持与尚未支持的故障恢复，不声称全面生产可用。
- **提交建议**：`docs: publish reliability matrix and limitations`；必要时 `fix: ...`。
- **面试主题**：数据库连接池、异步阻塞、线程与协程。

## 第 7 周：有证据的优化与发布预演

### Day 43 — RAG 消融实验

- **实现**：固定开发样本、模型和 prompt，比较向量、混合、混合+重排；选做第二组 chunk 参数；记录 Hit@5、答案质量和耗时；每次只改变一个因素。
- **AI 协作**：让 AI 帮你解释失败分类；本人验证结论是否被数据支持。
- **验收**：一张包含真实数字的实验表和明确默认配置；没有显著差别就选择成本更低或更简单的方案。
- **提交建议**：`eval: run retrieval ablations`；`perf: select measured retrieval defaults`；`docs: explain retrieval experiment results`。
- **面试主题**：什么是消融，为什么不能同时改五个参数？

### Day 44 — 简单流程与 Agent 的边界

- **实现**：增加一个确定性 FAQ 路径或规则路由；将单步问题与多工具问题分组；比较全部走 Agent 和分流方案的质量、调用数、成本。
- **AI 协作**：让 AI 提议分类边界和错误路由案例；你定义低置信场景的回退方式。
- **验收**：不会为了省调用把复杂问题错误截断；报告每一组的收益与损失。
- **提交建议**：`feat: route simple and complex support tasks`；`eval: compare workflow and agent paths`；`docs: record routing decision`。
- **面试主题**：什么情况下不应该用 Agent？

### Day 45 — 模型路由或单模型优化

- **实现**：有预算时接第二个模型，比较固定开发子集；无预算则比较单模型上下文长度/输出预算。记录工具 schema 兼容差异，不只替换 base URL。
- **AI 协作**：让 AI 根据错误样例分析是否值得 fallback；本人检查路由是否以牺牲质量换取表面低价。
- **验收**：至少一种受控对照；保存全部配置；质量下降超过预先约定范围时撤回优化。
- **提交建议**：`eval: compare model or context configurations`；`feat: add measured routing policy`（有效才做）；`docs: publish model tradeoffs`。
- **面试主题**：模型 fallback 会给工具调用带来什么风险？

### Day 46 — 多 Agent 对照实验（选做）

- **实现**：只做两个角色：诊断与依据核验；用结构化交接，限制总步数。与单 Agent 在相同开发样本上比较成功率、调用数、延迟和成本。
- **AI 协作**：让 AI 检查第二个角色是否增加了独立信息，还是重复生成；本人避免把多角色名称当作能力提升。
- **验收**：留下可复现实验；没有明显收益就不合并为默认流程。时间紧则改做最高优先级缺陷。
- **提交建议**：`experiment: compare verifier agent with baseline`；`docs: report multi-agent tradeoffs`；必要时 `fix: ...`。
- **面试主题**：多 Agent 什么时候增加价值，什么时候增加失败点？

### Day 47 — 部署候选版本

- **实现**：完善容器构建、健康检查、迁移与 seed；在干净环境执行启动；选择可用主机部署候选版，配置 HTTPS、服务端环境变量与持久卷。
- **AI 协作**：让 AI 审核 Docker/部署配置；本人检查镜像与前端构建产物不含密钥。没有部署预算时先完成本地可复现与录像，再安排托管。
- **验收**：外部网络可访问候选环境；重启不丢工单和状态；启动步骤与 README 一致。
- **提交建议**：`build: prepare release containers`；`ops: add deployment configuration`；`docs: verify clean setup instructions`。
- **面试主题**：容器镜像、数据卷、健康检查分别做什么？

### Day 48 — 演示隔离、限额与回滚

- **实现**：每个访客会话分配隔离空间；添加调用额度、请求频率和会话过期；公共页面不暴露管理员入口；写数据库备份/恢复与上一版本回滚步骤。准备明确标注的 replay 模式。
- **AI 协作**：让 AI 按匿名访客检查可访问资源；本人用两个浏览器会话验证隔离。只需支持当前低流量演示，避免扩张成商业账户系统。
- **验收**：访客互不可见；额度到达后给出明确提示；回放和实时模式可区分；能回到上一版本。
- **提交建议**：`feat: isolate and limit public demo sessions`；`feat: add labeled demo replay`；`ops: document recovery and rollback`。
- **面试主题**：公开 Demo 与生产系统的差异有哪些？

### Day 49 — 轻量候选版演示

- **实现**：让朋友或自己使用全新浏览器按 README 体验；记录卡住的地方；停止新增功能，发布 release candidate。
- **AI 协作**：让 AI 基于实际反馈排序缺陷，优先影响演示成功的问题。
- **验收**：三条演示脚本均能执行；遗留问题有记录与严重程度。
- **提交建议**：`docs: record release candidate feedback`；`fix: resolve demo blockers`。
- **面试主题**：你会如何决定上线前必须修哪些问题？

## 第 8 周：最终评测、GitHub 包装与面试

### Day 50 — 运行冻结测试集

- **实现**：锁定候选版本后运行 60 条最终测试；预算允许对随机性明显的子集重复 3 次；保存原始结果与失败样例。对少量安全案例的 0 次违规不能推论全面安全。
- **AI 协作**：AI 帮你统计和归类；本人复核关键错误。若依据结果调参，原结果保留，后续结果标作回归结果。
- **验收**：报告有实际分母、配置、SHA 和限制；没有达到目标也诚实发布，并解释主要瓶颈。
- **提交建议**：`eval: publish frozen test results`；`docs: analyze final failure cases`；`fix: address release-critical defect`（如有）。
- **面试主题**：重复实验、方差、置信区间的直觉。

### Day 51 — 整理 README 与架构说明

- **实现**：README 首屏放一句话价值、Demo、GIF、快速启动；补技术架构、数据来源、评测结果、限制和路线图；整理已有 ADR，不为数量写空文章。
- **AI 协作**：让 AI 优化表达和英文摘要；本人逐句核对能力声明与链接。
- **验收**：读者 30 秒知道做什么、3 分钟知道亮点、按步骤能运行；没有虚构生产用户与指标。
- **提交建议**：`docs: rewrite readme for reviewers`；`docs: finalize architecture decisions`；`docs: add concise english overview`。
- **面试主题**：怎么用事实解释技术选择而不是堆名词？

### Day 52 — 录制演示与发布素材

- **实现**：录制 3–5 分钟视频：正常排障、批准建单、失败恢复、越权拒绝、评测报告；导出小 GIF；截图展示引用、审批和轨迹；检查素材没有密钥。
- **AI 协作**：让 AI 写讲解提纲，你用自己的话讲，并让每一段对应真实画面。
- **验收**：录屏顺畅，失败案例有说明；视频中能看到应用状态变化；链接可访问。
- **提交建议**：`docs: add product demo and screenshots`；`docs: add live demo script`。
- **面试主题**：90 秒版本与 5 分钟版本的项目介绍。

### Day 53 — 简历与项目深挖

- **实现**：用实际技术与结果写 3 条简历项目经历；准备 RAG、审批幂等、可靠性三个故事，结构是问题 → 选择 → 证据 → 限制；整理最常被追问的代码位置。
- **AI 协作**：让 AI 质疑每个“提升”“稳定”“可扩展”的表述；没有数据就改写，不编百分比。
- **验收**：能解释简历中的每个名词；数据能在 reports 中找到；能回答项目最失败的一次尝试。
- **提交建议**：`docs: add verified project summary`；`docs: map interview topics to evidence`。
- **面试主题**：你做了什么、AI 做了什么、你如何验证？

### Day 54 — 编码与系统设计模拟

- **实现**：不看 AI，写一个限并发调用器或 TTL 缓存，并给出测试；写查询慢任务/重复工单的 SQL；画流量扩大 100 倍后的瓶颈与演进图，不必真正实现。
- **AI 协作**：先独立完成，再让 AI review 和追问。重点检查 race condition、异常传播和复杂度。
- **验收**：能现场修改一个真实缺陷；能解释 PostgreSQL、worker、模型配额分别怎样成为瓶颈。
- **提交建议**：`docs: record system design exercise`；`test: add regression for discovered defect`；`fix: ...`（有真实问题时）。
- **面试主题**：并发、索引、缓存一致性、限流、扩容取舍。

### Day 55 — 最终发布 v1.0.0

- **实现**：干净环境复跑快速启动、CI 和 3 条 E2E；核对 License、依赖声明、数据说明、环境模板；扫描待发布内容中的密钥；打 tag，写真实 release notes，部署最终版本。
- **AI 协作**：让 AI 做一次发布审阅，只修会影响交付的缺陷；不要临时升级所有依赖或新增框架。
- **验收**：GitHub、在线演示、视频、报告四个入口可用；工作目录干净；出现问题可以回滚。
- **提交建议**：`chore: prepare v1.0.0 release`；`docs: publish release notes and known issues`；必要时 `fix: ...`。
- **面试主题**：发布验证与可回滚变更。

### Day 56 — 项目复盘与求职映射

- **实现**：整理 8 周记录、3 个最有价值的实验、3 个未解决问题；找 10–15 条匹配自己经验的 JD，给每条要求标记已证明/理解但无实证/待补；形成下一阶段 2 周补强清单。
- **AI 协作**：让 AI 依据你的仓库和真实报告做 30 分钟模拟面试，追问证据；答案由你先讲。
- **验收**：项目可演示、可复现、可解释；缺口明确；不会因为“做完 56 天”就宣称满足所有资深岗位。
- **提交建议**：`docs: publish final retrospective and next steps`。
- **面试主题**：如果重做，你会删掉什么？最值得保留的设计是什么？

## 每日记录模板

```markdown
# Day NN：主题
- 今天的 Issue / PR：
- 目标和验收标准：
- 实际完成：
- AI 协助部分：
- 我修改或否决了 AI 的什么建议，为什么：
- 验证命令与结果：
- 新发现的失败案例：
- 学到的概念（用自己的话）：
- 未完成与下一步：
```

不必为每一天单独提交一篇长文章；同一周的记录可放在同一文件里。提交记录的价值来自可追踪的工程进展、修复和实验。
