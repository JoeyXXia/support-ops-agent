# SupportOps 运行故障定位

1. 从运行详情复制 run ID。页面同时显示创建该运行的请求 ID 和各操作耗时。
2. 以运行所有者的会话查询 `GET /runs/{run_id}/trace`。先看 `failure.step_id`、`failure.operation` 和 `failure.error_code`，再按事件顺序查看重试与模型、工具或检索耗时。运行在进入首个步骤之前失败时，步骤可能为空。
3. 查看 `write`。草稿与已提交的工单分别记录。 `ticket_receipt=committed` 表示已查到数据库事务写入的执行回执。 `no_receipt` 表示查询时未找到回执，适用于分析已停止的运行。 `unverified` 表示内存模式缺少持久证据。遇到失败但已有回执时，沿用原审批和工单详情查询结果。
4. 需要跨进程核对时，用 run ID 过滤 API 与 worker 的结构化日志。创建请求和 worker 操作共享创建请求 ID；后续审批有独立 HTTP 请求 ID，同一个 run ID 将两者关联。

```powershell
docker compose logs --no-color api worker | Select-String -SimpleMatch '<run-id>'
```

日志仅输出生成的关联标识、已知操作、耗时、尝试次数和错误分类。日志与诊断响应不含原始问题、工具输入、工具正文或 SDK 原始请求 ID。正常运行详情依旧向所有者提供业务数据。Compose 关闭了通用 URL 访问日志；手动启动 Uvicorn 时可使用同样的 `--no-access-log` 参数。

日志用于核查一次运行的事件。指标适合观察失败率与延迟分布。Trace 关联一次请求在各步骤的时间和因果。当前使用本地结构化日志与已有数据库，无外部 trace 服务。
