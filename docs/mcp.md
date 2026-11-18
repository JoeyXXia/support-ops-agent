# SupportOps 本地只读 MCP

日期：2026-11-18。实现为独立 Python 进程，使用官方 `mcp==2.3.0` SDK 和 stdio，不是将现有 FastAPI 接口改名。本日只有两个现有只读工具；网页、Agent loop 和 LangGraph 仍使用原有直接执行路径。

## 启动与复现

在仓库根目录、现有 Python 3.12 环境中执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m apps.mcp.client --subject user-alpha
.\.venv\Scripts\python.exe -m apps.mcp.client --subject user-beta
.\.venv\Scripts\python.exe -m apps.mcp.client --subject support-alpha
.\.venv\Scripts\python.exe -m apps.mcp.client --subject support-beta
```

每条命令由官方 Client 启动固定的本地 server 子进程，完成发现、两项读取和错误检查后退出。输出是验收 JSON，不需要启动网页、数据库、模型 API 或 MCP Inspector。CloudDesk 用量与错误日志是已有 synthetic 样例，不是外部客户实时数据。

若由受信任的本机 Host 托管 server，固定启动项为当前虚拟环境的 Python 可执行文件、参数 `-u -m apps.mcp.server`、工作目录为仓库根目录，环境变量 `SUPPORTOPS_MCP_SUBJECT=user-alpha`。直接在终端执行以下命令会等待协议输入，不是交互式问答工具：

```powershell
$env:SUPPORTOPS_MCP_SUBJECT = 'user-alpha'
.\.venv\Scripts\python.exe -u -m apps.mcp.server
```

server 不读取模型提供的身份、不复用 HTTP Cookie，也不自动读取 `.env.local`。没有启动主体或名称未知时，stderr 给出配置提示并以2退出，stdout 为空。正常 stdout 专用于 SDK 的协议消息；诊断只写 stderr。

## Host、Client、Server 与业务层

Host 管理应用侧的模型、用户交互和连接选择；Client 是 Host 侧的协议连接；Server 提供可发现的能力。协议不替代业务授权、审批、数据库事务或模型效果验证。[MCP 架构说明](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture)

本日最小客户端不是完整 Host：它只验证协议，不请求模型。`apps/mcp/client.py` 用官方 Client 与 StdioServerParameters 管理进程与协议；`apps/mcp/server.py` 使用官方低层 Server 的列举与调用处理器，业务继续交给 `packages/tools/executor.py`。选择低层接口是为了直接复用已有严格 JSON Schema，避免包装函数的参数转换改变原来严格验证的语义。[低层 Server](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/)

SDK 版本是2.3.0，本轮四个主体均实际协商到 `2026-07-28`。发现与调用由 SDK 执行，没有自写 JSON-RPC 解析器；不把较早版本的初始化流程套用为本次实际交互。没有注册到 Codex/桌面 Host，也未验证第三方 Host 或旧版本兼容性。[SDK 客户端](https://py.sdk.modelcontextprotocol.io/client/)

## 两类工具 schema

| 项目 | MCP 工具 | 模型 API 工具 |
| --- | --- | --- |
| 用途 | Client 发现 Server 的能力，并通过协议调用 | 把可用函数描述交给模型，模型提出调用 |
| 本项目描述 | Tool 的 name、description、inputSchema、outputSchema、annotations | 现有 ToolDefinition 经模型适配层转换为对应 API 的工具描述 |
| 共享部分 | inputSchema 来自现有 Pydantic arguments 模型 | parameters 来自同一业务参数模型 |
| 不能推导的结论 | schema 不提供租户授权，readOnlyHint 不负责拦截副作用 | 模型产生调用不代表已授权或已执行 |

`get_workspace_usage` 只接受 workspace_id；`get_recent_errors` 增加1–20的严格整数 limit。额外 user_id、tenant_id、role 会失败，布尔值或字符串不会悄悄转换成整数。现有 ToolExecutor 实际执行严格验证，不能仅依赖 MCP 工具广告。

成功和业务失败保留现有 `{tool, ok, data, error}` 语义，分别采用只读结果子类型限定数据类型。MCP 响应同时提供 JSON 文本 content 与 structuredContent，并设置 isError。SDK Client 检查已声明的输出 schema，冒烟还逐例比较直接执行与跨进程结果一致。[结构化输出](https://py.sdk.modelcontextprotocol.io/servers/structured-output/)

## 错误分层

| 情况 | 实际处理 |
| --- | --- |
| 正常读取 | isError=false，结构化结果与现有直接工具结果一致 |
| 缺参数、额外身份字段、limit 类型/范围不符 | isError=true，error.code=invalid_arguments |
| 其他租户或不存在的工作区 | isError=true，均为 not_found，不提供存在性区别 |
| 未列举的 get_ticket、search_docs、propose_ticket 或 create_ticket | 入口白名单拒绝，JSON-RPC 错误 -32602，Client 抛 MCPError |
| 未预期的执行异常 | isError=true、internal_error，响应与日志不包含原异常文本 |
| 缺少/未知启动主体 | 进程以2退出，不输出伪造协议响应 |

业务失败由模型/调用者按结果处理，协议错误由 Client 连接层识别；不能只判断有没有文本，也不能把所有错误都当可重试。SDK 2.3.0 的错误码位于 `MCPError.error.code`。[SDK 错误处理](https://py.sdk.modelcontextprotocol.io/servers/handling-errors/)

## 本地信任边界

一个 server 进程固定一个受信任启动主体。角色与支持租户分配由现有种子资料派生，support 只分配到自己的种子租户。工具参数和请求 `_meta` 不能切换主体；手写调用未列举工具也过不了白名单。

`SUPPORTOPS_MCP_SUBJECT` 是本机启动配置，不是凭据或远程用户认证。能控制本机启动命令、环境或代码的人处在本日信任边界内，可以选择种子主体；不能把该模式公开为多用户远程服务。网页登录身份如何受控传入 MCP 调用属于 Day26，今天未接入。远程 OAuth、HTTP MCP、写工具和任意第三方 server 均未实现。

客户端只启动固定可执行文件和固定模块，不接受任意命令或 URL；明确传递的环境只有主体名与 UTF-8 设置。SDK 再加必要的操作系统环境白名单，未转发应用的模型密钥和数据库 URL。[stdio 传输与环境](https://py.sdk.modelcontextprotocol.io/client/transports/)

## 验证与限制

`python scripts/check_offline.py` 已包含 apps/mcp 格式和静态检查，以及真实 SDK stdio 契约测试。单独执行可用 `python -m unittest discover -s tests -p test_mcp.py -v`。冒烟每个主体检查两项正常读取、跨租户、严格布尔 limit、参数身份伪造、元数据身份伪造和未暴露写工具，退出 Client 上下文后关闭 server。完整结果见 [Day25 验证](../reports/day-25-validation.json) 和 [开发日志](devlog/2026-11-18-day-25.md)。

首个版本发现 datetime 转换边界：把工具结果先转 JSON 字符串日期，再做严格 Python 校验，会失败。当前先用 Python 对象校验结果，再在协议边界转 JSON；有实际错误日志数据的测试覆盖，未通过放松 schema 解决。

最小客户端为本机验收设置单请求5秒和整体25秒等待保护；未注入跨进程挂死、强杀父进程等系统故障，不宣称覆盖 Day26 的 Agent 超时/取消策略。没有真实模型质量、生产吞吐或网络延迟指标。前端和浏览器本日未改动，未重复前端验收。

依赖只新增官方裸 SDK 及其必需依赖，不安装 CLI extra、Inspector 或另一个 FastMCP 包。原有包版本实测未变化；pywin32 使用 Windows 平台标记。标记已在本机验证不同平台的计算结果，但没有本地 Linux 或托管 CI 通过声明。[锁定包发布页](https://pypi.org/project/mcp/2.3.0/)
