# Day 9：切块、embedding与恢复

本模块是受信本机操作员CLI，不接收HTTP自报身份，不是Agent工具，也不提供向量搜索。先按database.md准备PostgreSQL，安装原有锁定依赖并执行alembic upgrade head。没有新增Python依赖或服务。

## 切块输入与出处

切块使用数据库修订的规范化body：UTF8 BOM与换行在Day8导入时已经处理。字符偏移是Python Unicode字符串索引，start_char包含、end_char不包含；行号从1开始且两端包含。不是UTF8字节偏移，也不是JavaScript UTF16索引。必须用存储的版本body定位，不能对另一个换行版本的磁盘文件直接套偏移。

策略heading-paragraph-v1:800:100识别ATX标题、反引号或波浪线围栏，标题内C#保留，闭合标题标记按规则去除。按章节分割，再在段落边界合并到最多800字符；只有超长普通段落按窗口切分并重叠100字符，不跨章节重叠。代码块、含管道符段落按保守表格候选视为原子块，超过800字符拒绝，未闭合围栏拒绝。不是完整CommonMark解析器：Setext标题、HTML/缩进代码等没有专门支持，管道符检测可能把普通文字当作表格。

每块保存version_id、ordinal、body、标题路径、起止字符/行号、body SHA256、实际embedding输入SHA256、切块policy和embedding_id。输入为标题路径以“ / ”连接、两个换行、原样body；不额外插入其他租户内容。标题已在body里时可能重复，这是显式输入策略heading-prefix-v1，不是经过检索效果优化的最佳参数。

发送前单条UTF8字节数不超过8192，是针对所选byte-token模型的保守上界，不是精确token计数；每批最多16条。没有引入tokenizer库，也不把800字符称为800token。元数据导言仍单独成块，有追溯价值但检索信息量低，后续需评测再决定过滤，不能声称分块已调优。

## embedding与缓存

真实provider固定OpenAI/text-embedding-3-small/1536维，沿用已授权.env.local凭据；已有进程环境优先，SDK max_retries=0、timeout=20，应用最多3次尝试，退避0.25/0.5秒加最多0.1秒抖动。仅连接/超时、临时429与5xx重试；额度耗尽、认证、请求错误和异常输出不重试。当前无全库费用预算器，不要对大批私有材料盲目使用--live。

验证数量、model、索引集合、维度、数值类型与有限性，拒绝全零向量；合法乱序返回按index恢复输入顺序。usage仅在真实响应提供时记录；mock为None，不编造token。失败只暴露安全分类与请求ID。响应无效后不缓存整批，失败后CLI停止继续处理后续文档。

embedding_cache实际存vector(1536)，唯一键包含scope_key/provider/model/dimensions/input_version/input_hash。公共仅与公共共享，私有材料只在同租户范围共享；不因为正文同哈希就跨租户复用。缓存的是精确输入，不按文件路径猜。模型或provider不同不复用，mock使用独立mock/fixture-embedding-v1命名空间，只有确定性数值，没有语义意义。

## 状态、事务与并发

文档修订的embedding_status为pending/processing/ready/failed；embedding_profile指明当前尝试的切块/provider/model/维度/输入策略，embedding_error只存分类。每修订用PostgreSQL session advisory lock限制单个writer，忙则拒绝；短事务更新状态，网络请求不占用数据库行锁或长事务，但锁会占用一个连接。

每个成功批次独立事务持久化缓存。全部向量齐备后，一个事务发布chunks并标ready；重复处理同修订的chunk序号保持UUID稳定。批次中途失败时已缓存批次保留，新chunk不部分发布；发布失败回滚chunks变更。重跑读取缓存，补算缺失批次。

同版本只能有一个当前profile。切换到真实provider失败时，旧mock chunk记录可能仍在表内，但修订状态failed；消费者必须同时过滤ready、profile与provider，不能直接把表里旧行当作本次成功。当前没有检索接口。成功缓存也可能成为暂时孤立数据，没有GC。

进程崩溃会释放连接锁，但processing状态可能保留；重跑会重新取得锁并恢复。DB不可用时状态写入本身也可能失败，不能保证所有故障都有failed持久记录。不同修订并发遇到同一缓存未命中，可能重复付费请求；唯一约束只防重复存储，未实现跨修订single-flight或exactly-once API调用。

## 命令与验证

`scripts/process_documents.py preview`离线导出8篇原始文档的切块，包含原文位置；`embed`默认mock处理数据库中每个可见文档身份的最高修订编号，最多8篇；--tenant-id限定可信操作员范围；--limit为1至100；--batch-size为1至16。历史修订不自动重算，也没有独立手选修订ID的CLI。

真实调用必须显式--live；--live --limit 1只处理第一篇公共文档，不是完整语义评测。默认离线门禁60项，真实PostgreSQL测试15项（含Day8的8项）。集成测试仍强制_test库和随机schema，无API密钥，不用SQLite替代。

迁移0002不修改0001；升级要求Day8占位chunks为空，非空则明确失败，不能静默丢数据。回退0002会删除新列和缓存表，但保留基础chunk正文行；再次升级前须在可丢弃/维护环境明确重建派生chunk数据。没有自动清除已有chunk或生产回退保证，不能直接对非空库随意down/up。

真实验收失败与mock验收分开记录在Day9报告；原始PDF/v0.1-loop标签不更新，按每7天手册任务另出版本。数据、密钥、API错误正文不写进公开日志；本项目只把公共synthetic切块预览纳入报告。
