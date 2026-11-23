# Day30 行为评分与证据格式

使用冻结开发集的 case ID，把受控测试脚本采集的观察和人工设定的规则交给评分器。评分关注工具与持久状态，HTTP 状态和回答文字不会参与通过判定。

- `expected` 是受信任的用例规则。声明预期工具顺序及参数、步骤和实际尝试数上限、终态和原因，以及允许写入的工单数量。要求建单时明确目标租户、工作区与工单状态。
- `observation.attempts` 是调用工具时记录的参数与成功位，同一 call_id 的失败后重试仍分别占用调用预算。评分直接使用仓库注册表的严格 schema。格式正确但不是预期 workspace_id 或 limit 也会失败。
- `observation.ticket` 是运行返回的工单。另从隔离测试数据库读取新增的工单行与执行回执，放入 `committed_tickets` 和 `receipt_ticket_ids`。空列表表示已确认为零，null 表示缺少证据。非空工单必须同时匹配两路事实。
- 可选 `ranked_sources` 是已经按产品版本和权限过滤的排序快照。检索单独记录公开来源 Hit@K 与逐例倒数排名 RR@K，不计入行为结果。版本或有效来源标签缺失时排除该指标。

从仓库根复现。

```powershell
python -m scripts.score_day30 evals/day30/calibration.json output/day30-recheck.json
python -m unittest discover -s tests -p test_behavior_scoring.py -v
# 配置专用 _test 库后
python -m unittest discover -s tests/integration -p test_behavior_scoring.py -v
```

输出地址应为未存在的文件。示例 11 条观察是故意构造的校准输入，含 7 条应失败反例和 4 条正常对照。它们来自 5 个开发 case ID，同一 ID 可出现多个独立观察。真实数据库验收用确定性 mock 模型、真实工单和真实回执。当前不做最终集评估或回答质量评分。

评分器信任受控测试入口与规则文件。直接传入客户端返回值、模型输出或生产表统计，不能替代工具尝试与隔离持久证据。测试已重用已有 PostgreSQL 容器和 schema 隔离方式。
