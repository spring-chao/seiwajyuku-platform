# 生产专职人员新增 Network Error 专项修复验证报告

## 结论

本次故障不是浏览器网络、CORS、灰度路由或 FastAPI 容器启动问题。生产请求已经到达 API，并在创建 IAM2 专职人员的授权记录时触发 MySQL 外键错误，最终以 500 返回，前端因此显示为 `Network Error`。

代码修复、生产迁移和 API 发布已完成；本次没有创建真实员工账号。生产业务验收仍需由运营人员使用一个新的唯一账号完成。

## 生产只读证据

- CloudBase 版本 237 在 2026-09-09 20:06 左右收到 `POST /api/v1/staff-management/staff`，响应为 500；同一接口的 OPTIONS 和 GET 正常。
- 应用堆栈落在 `app/services/staff_management.py` 的 `_insert_grants`，具体异常为：`pymysql.err.IntegrityError: (1216, 'Cannot add or update a child row: a foreign key constraint fails')`。
- 写入目标是 `employee_authorization_grants.role_key -> roles.role_key` 外键。岗位映射使用的是 IAM2 员工角色键，而生产数据库未必已经有对应的角色目录记录；代码此前只校验静态角色键，没有在写入前校验数据库中的 active 角色行。
- 因此“网络错误”是后端 500 的前端表现，不是请求没有到达服务。
- 生产数据库位于私有网络，本次没有绕过安全边界建立直连，也没有查询或修改生产数据；上述结论来自 CloudBase 应用日志和代码外键定义。

## 代码与数据库改动

### 1. IAM2 角色目录迁移 0054

新增：

- `migrations/mysql/0054_iam2_staff_role_catalog.sql`
- `migrations/sqlite/0054_iam2_staff_role_catalog.sql`
- 对应 MySQL/SQLite loss-averse rollback 文件

迁移幂等地补齐普通专职人员可分配的 10 个角色（含 `read_only`）及其角色权限，并将角色恢复为 active。迁移只涉及 IAM2 的 `roles`、`permissions`、`role_permissions` 和 `schema_migrations`，不修改 Volunteer 2.0 表或数据。

### 2. 写入前角色目录校验

`_normalize_grants` 现在会在创建或更新任何专职人员记录前，确认每个授权角色在 `roles` 表中存在且 active。缺失时返回明确的 400：

> 岗位权限尚未配置，请联系管理员完成 IAM2 角色初始化

校验位于事务中、任何人员记录写入之前，因此不会留下半条账号、人员或任职记录。

### 3. 完整性错误的安全响应

专职人员接口现在将 SQLite/MySQL 完整性错误转换为明确 400：

- 重复账号或手机号：提示已存在；
- 外键关联错误：提示重新选择岗位、机构或人员关联；
- 其他完整性错误：提示联系管理员。

原始 SQL、表结构和数据库异常不会返回给前端。

### 4. MySQL 隔离验证门禁

`scripts/validate_staging_mysql.py` 现在会在真实 MySQL staging 中先检查 0054 角色目录和角色权限数量，再创建一个临时 IAM2 专职人员、确认 `employee_learning_management + r1/SUBTREE` 授权正确，最后清理测试记录。该脚本的安全门禁只允许本机/CI staging 数据库。

## 验证结果

| 验证项 | 结果 |
| --- | --- |
| Python 编译检查 | PASS |
| SQLite 全量迁移与 0054 角色/权限验证 | PASS |
| 缺失角色时 400 且零写入 | PASS |
| 模拟最后一步外键错误时 400 且事务回滚 | PASS |
| IAM2 专职人员专项测试 | PASS（15 项） |
| 后端全量测试 | PASS（560 passed、2 skipped） |
| GitHub 隔离 MySQL staging 验证 | PASS（CI run 34354770888：迁移、角色目录、专职人员新增/清理、回滚保护） |
| 生产数据库快照 | PASS（CloudBase snapshot `10494171`，`IsUsable=yes`，2026-09-09 21:20 完成） |
| 生产 0054 MySQL 迁移 | PASS（`schema_migrations` 已记录；10 个系统角色 active；角色权限目录 66 条） |
| API 生产部署 | PASS（CloudRun revision `238`，BuildId `2602272801`，镜像摘要 `sha256:9323a5c338b9a41747440733824f1a61d665f9b4923b4b9b639b86cbc7f828dd`） |
| 灰度发布 | PASS（5% 灰度观察通过后提升至 100%；旧 revision `237` 保留） |
| 生产健康与鉴权探针 | PASS（4 个健康入口各 20/20 为 200；工作人员列表未登录为 401） |
| 真实人员新增验收 | 待人工执行（本次未创建真实员工） |
| Volunteer 2.0 代码、数据和权限链路 | 未修改 |

## 后续执行顺序

1. 使用一个新的唯一账号完成一次真实新增验收；不要重复提交原测试人员。
2. 验收时确认姓名、性别（男/女）、手机号、至少 6 位密码、岗位和组织范围均能保存，并能正常登录。
3. 若真实新增仍失败，保留请求时间和响应码，优先核对角色目录、机构范围和 CloudBase 日志，不扩大写入或修改 Volunteer 2.0。

## 生产发布记录（2026-09-09）

- 发布前先唤醒处于 Serverless pause 状态的数据库；只读 `SELECT 1` 成功后创建快照，未绕过数据库安全边界。
- 0054 迁移按 MySQL 文件逐条执行并写入 `schema_migrations`，随后只读核验角色目录；没有创建真实员工、没有写入志工或其他业务数据。
- CloudBase 候选 revision `238` 先等待 `CreateVersion=finished`、版本 `normal` 和管理任务可切流，再执行 5% 灰度；健康与未授权探针通过后执行 `promote` 至 100%。
- 发布后 `/__tcb_probe__`、`/health/live`、`/health`、`/api/v1/health` 均连续返回 200；`/api/v1/system/environment` 仍为 production，既有运行门禁保持原值。
- `/api/v1/system/build-info` 当前仍返回 `unknown` 字段；CloudBase 构建记录已保存真实 BuildId 和镜像摘要，运行时 provenance 注入作为后续独立优化，不影响本次功能发布。
