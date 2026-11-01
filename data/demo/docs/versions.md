# 版本与升级

文档 ID：versions
数据版本：clouddesk-demo-v1
Synthetic：true（自写虚构材料）
范围：所有演示租户的公共产品规则

## versions

适用版本：1.0、2.0。1.0 的429只返回HTTP状态，需要用量和日志辅助判断。2.0 增加 reason 字段；分钟限流附带 Retry-After（秒）。升级版本不重置月额度，也不自动改变套餐。
