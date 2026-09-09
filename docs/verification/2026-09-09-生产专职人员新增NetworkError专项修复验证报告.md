# 生产专职人员新增 Network Error 专项修复验证报告

## 结论

本次故障不是浏览器网络、CORS、灰度路由或 FastAPI 容器启动问题。生产请求已经到达 API，并在创建 IAM2 专职人员的授权记录时触发 MySQL 外键错误，最终以 500 返回，前端因此显示为 `Network Error`。

已完成代码修复和本地回归验证；本任务没有执行生产部署、生产数据库迁移或真实人员新增。生产修复仍需在后续获批发布窗口中先执行 0054 数据库迁移，再进行一次真实业务验收。

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
| 生产数据库迁移 | 未执行 |
| 生产部署/灰度/真实人员新增 | 未执行 |
| Volunteer 2.0 代码、数据和权限链路 | 未修改 |

## 后续执行顺序

1. GitHub `mysql-staging` CI 已通过，进入发布审批，不等同于生产已修复。
2. 获批后在生产发布窗口执行 0054 迁移，先只读核对 10 个角色均 active 且角色权限齐全。
3. 迁移完成并确认 API 健康后，只用一个新的唯一账号进行一次真实新增验收；不要在迁移前重复提交原测试人员。
4. 若真实新增仍失败，保留请求时间和响应码，优先核对角色目录、机构范围和 CloudBase 日志，不扩大写入或修改 Volunteer 2.0。
