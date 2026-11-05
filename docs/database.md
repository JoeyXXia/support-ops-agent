# Day 8：Markdown 文档持久化

此模块与现有 mock 页面、RunStore 和两个只读工具独立；它们仍不依赖数据库。仅可信本机操作员 CLI 可导入/查询，未开放 HTTP 或 Agent 写工具，没有接入登录、RLS 或访客隔离。

## 依赖与配置

Python 依赖：SQLAlchemy Core 2.0.57、psycopg/binary 3.3.8、Alembic 1.20.1，版本在锁文件中。没有 ORM 实体层，也没有额外向量 Python SDK。数据库实际验收环境为 PostgreSQL 17.11 + pgvector 0.8.7。

安装项目依赖后，先准备启用了 pgvector 的 PostgreSQL。数据库管理员创建 `supportops` 与独立的 `supportops_test` 数据库，运行迁移的角色须能在目标库安装 `vector` 扩展及创建表。不要给公开应用沿用迁移超级用户。

复制无凭据模板或手动新建忽略文件 `.env.database.local`：

```dotenv
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@127.0.0.1:55432/supportops
```

将 USER/PASSWORD 替换为自己的数据库账号；密码含保留字符时使用 URL 百分号编码。这里只读取 `DATABASE_URL`，不会读取或改写 OpenAI 密钥。已有进程环境优先。不要把数据库 URL 放入 `VITE_*`、提交或日志。

## 首次迁移、导入与查询

从项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -X utf8 scripts/import_documents.py import --scope public --product-version 1.0 --product-version 2.0
.\.venv\Scripts\python.exe -X utf8 scripts/import_documents.py list --scope public
.\.venv\Scripts\python.exe scripts/documents_smoke.py
```

8篇现有文档是公共产品规则，统一导入public；不会自动复制到两个租户。`--scope tenant --tenant-id tenant-alpha`适用于操作员明确归属的私有测试材料。两个范围参数来自可信CLI操作员，不能用于未来HTTP调用方自报权限。

导入目录默认 `data/demo/docs`，可通过 `--root` 与 `--directory` 显式指定隔离副本。目录只读取第一层 `.md`，每批1至100篇，每篇不超过1 MB；拒绝根目录外路径（包括解析后越界的符号链接）、非UTF-8、NUL、重复/缺失元数据、ID与文件名不匹配及非synthetic材料。格式沿用现有中文元数据行与一个一级标题，不是通用Markdown解析器。

适用产品版本由操作员显式传入，排序去重后只接受当前事实表中的1.0/2.0；不从自然语言正文猜测版本，也不验证正文与操作员指定版本在语义上是否一致。导入前需人工审核范围与内容。

## 三张表与唯一性

- `documents`：UUID、document_key、tenant_id、非空scope_key。公共为`public`，租户为`tenant:<id>`；CHECK保证它与tenant_id一致；UNIQUE(scope_key, document_key)避免NULL租户的唯一性歧义。
- `document_versions`：UUID、document_id外键、正整数version_number、正文、标题、相对source_path、数据版本、产品版本列表、synthetic、时间与两个哈希。UNIQUE(document_id, revision_hash)和UNIQUE(document_id, version_number)分别限制重复修订与编号冲突。
- `chunks`：UUID、version_id外键、非负ordinal与正文，UNIQUE(version_id, ordinal)。今天只迁移建表，不写chunk、不选择向量维度、不创建向量索引。

文档修订编号、data_version、product_versions含义不同。主表按范围+文档ID标识身份；来源路径保留在版本上，文件移动也可以成为元数据修订。

正文去除UTF-8 BOM并把CRLF/CR统一为LF，再计算content_hash=SHA256；保留其他空白与结尾换行。revision_hash对content_hash、相对路径、data_version、排序后的产品版本、synthetic的规范JSON再做SHA256。只改变换行/BOM不建新版本，改变正文或上述元数据建新版本。

重新导入过去已存在的修订返回旧ID/旧编号，不另建“回退版本”，也不改变最新编号。当前查询返回可见范围的全部修订，不承诺“导入的最后一次就是当前生效版本”。没有当前发布指针或版本激活功能。

## 事务、并发与删除

整批一个事务，任何数据库错误都会回滚；解析在事务前完成。按document_key排序获取锁，先INSERT ON CONFLICT DO NOTHING创建身份，再SELECT FOR UPDATE锁定父文档，串行计算下一个修订编号。数据库唯一约束是最终兜底，不只依赖先查再插。

应用只追加版本、不更新旧正文；数据库角色仍可直接UPDATE，尚无防修改触发器或受限角色策略。query_versions只读取public加指定可信租户的数据；没有RLS，数据库管理员仍能看全量。

没有删除API。外键默认限制父记录删除，避免无意删除版本/chunk。`alembic downgrade base`会删除本模块三张表及其数据，仅供明确可丢弃数据库的维护/测试；不会自动DROP vector扩展，因为它可能被其他表使用。CI/集成测试只删除随机生成的测试schema。

## 当前机器的便携数据库

没有Docker或系统PostgreSQL服务。本次从[EDB官方二进制页](https://www.enterprisedb.com/download-postgresql-binaries)下载17.11 Windows x64便携包，从[pgvector官方源码](https://github.com/pgvector/pgvector/tree/v0.8.7)构建0.8.7；使用现有VS2022 C++工具，未安装新系统服务。

二进制、数据库文件、下载包、源码、构建产物、密码文件均在忽略的`output/day8-runtime`。中文路径initdb曾失败，改用 `%TEMP%\supportops-day8-pg` ASCII junction指向同一目录，数据库仍保存在项目output下。该别名和端口55432属于本机配置，不能复制为跨机器依赖。

本机重启/停止命令（操作对象是本项目隔离集群，不是系统数据库）：

```powershell
$runtimePath = Get-Content output/day8-runtime/ascii-path.txt
# 仅首次已初始化后使用，不要重复initdb
& "$runtimePath\pgsql\bin\pg_ctl.exe" -D "$runtimePath\data" -l "$runtimePath\postgres.log" -o "-h 127.0.0.1 -p 55432" -w start
# 需要停止时
& "$runtimePath\pgsql\bin\pg_ctl.exe" -D "$runtimePath\data" -m fast -w stop
```

数据库只监听127.0.0.1，使用随机密码与SCRAM。没有注册开机自启。Windows下不要用`Start-Process -Wait`启动pg_ctl树，否则可能等到整个数据库进程树退出；若需要隐藏启动，可用`Start-Process -WindowStyle Hidden`并只等待pg_ctl本身。

## 验证边界

默认离线门禁不连接数据库，新增6项解析测试与原40项回归一起执行。真正的PostgreSQL集成测试必须显式设置独立测试库，不用SQLite替代，不缺库跳过：

```powershell
# 连接到专用库；不要在命令中填入并公开真实密码
# TEST_DATABASE_URL通过本机安全环境设置，库名必须以_test结尾
.\.venv\Scripts\python.exe -m unittest discover -s tests/integration -v
```

测试在该库随机`day8_test_*` schema运行，校验迁移重复/回退、幂等、内容及元数据修订、回滚、范围、并发首次导入及修订、约束；测试用户需要建schema权限。CI新增单独PostgreSQL服务job，测试密码仅供临时CI库，不是本机凭据。远端结果以实际Actions运行记录为准。

恢复验证用两个新Python进程分别运行documents_smoke，中间实际重启PostgreSQL，比较全部公共版本的持久化摘要；不是只dispose连接冒充服务重启。摘要一致只证明这组数据在这次正常重启后保存，不证明备份恢复、崩溃一致性或生产性能。
