# 分钟限流排障

文档 ID：rate-limit
数据版本：clouddesk-demo-v1
Synthetic：true（自写虚构材料）
范围：所有演示租户的公共产品规则

## diagnosis

适用版本：1.0、2.0。429不等于月额度耗尽。先确认工作区、产品版本、套餐、分钟及月用量，再查询最近错误日志。2.0 的 reason=minute_rate_limit 表示分钟限流；按 Retry-After 秒数等待后有限重试。1.0 不能仅凭429确认原因。
