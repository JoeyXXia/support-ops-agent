# 月额度耗尽

文档 ID：monthly-quota
数据版本：clouddesk-demo-v1
Synthetic：true（自写虚构材料）
范围：所有演示租户的公共产品规则

## diagnosis

适用版本：1.0、2.0。2.0 的 reason=monthly_quota_exhausted 表示月额度耗尽。1.0 需要结合月用量和日志确认。耗尽后停止自动重试，等待每月首日00:00 UTC重置或人工调整额度。不要建议持续重试解决月额度耗尽。
