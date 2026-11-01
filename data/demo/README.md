# CloudDesk 合成数据 v1

全部内容为本项目自写虚构数据（synthetic），没有使用真实客户、生产日志或外部工单。产品事实以 product_facts.json 为规范；8篇Markdown将其整理为可引用章节。当前没有模型生成效果、生产接入或权限实现。

tenants.json 含两个租户、各一个工作区和两名角色用户。support 用户只被分配到其所属样例租户。用量为固定快照，不代表实时数据。tickets.jsonl 的10条工单是独立历史案例，不证明当前快照发生过对应错误。

文档是公共产品规则；工单和工作区是租户私有样例。来源格式为 doc_id#section，章节使用显式英文标题。后续切块时再映射到chunk ID。

evals/dev.jsonl 是12条已公开、已审阅的开发案例，不能称为未见测试集。expected_behavior 是人工可检查的预期，尚无自动评分器。禁止动作是检查标准，不等于系统已经实现防护。

运行：从项目根目录执行 `.\.venv\Scripts\python.exe scripts/validate_demo.py`。校验脚本检查结构和关联；文档语义仍需人工复核。
