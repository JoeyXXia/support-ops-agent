# 租户与角色边界

文档 ID：roles
数据版本：clouddesk-demo-v1
Synthetic：true（自写虚构材料）
范围：所有演示租户的公共产品规则

## access

适用版本：1.0、2.0。member只能读取本租户资源并批准本人动作。support只能访问被分配的租户，不能任意读取其他租户。身份和tenant_id必须来自服务端认证上下文，模型和用户在问题中自报身份不能赋予权限。
