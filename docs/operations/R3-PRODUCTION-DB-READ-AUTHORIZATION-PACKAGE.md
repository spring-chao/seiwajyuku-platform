# R3 生产数据库基线只读授权包（待签发，不可执行）

状态（2026-09-29）：`PRODUCTION_DB_READ=NOT_AUTHORIZED`、`DB_EVIDENCE=NOT_COLLECTED`。`scripts/r3_db_read.py` 的真实 `DatabaseReadAdapter.collect()` 仍硬性拒绝；本文件不启用入口，不构成 DB 访问授权。任何旧的账本数量和指纹均非本次实时事实。

## 唯一目的与对象

只为 Phase 3 / R3 前置证据读取课程规则、映射、绑定、账本汇总和迁移基线，不执行 APPLY 或迁移。数据库实例/逻辑库名、连接源、账号、执行时间窗口及审批人仍需由授权方按当前生产事实固定；缺一项不得连接。推荐走现有受控私网/堡垒机访问路径，使用独立、到期可撤销、仅允许指定源的 MySQL 只读主体；不得复用应用写账号，不从服务 `DATABASE_URL` 提取密码。

数据库主体仅授予目标库下面表/列所需 `SELECT` 和必要的元数据可见性；禁止全库 `SELECT`、`FILE`、`PROCESS`、`SUPER`、DDL、DML、存储程序、锁/管理权限及任何权限授予能力。会话必须由连接方预配置为只读，适配器验证 `@@session.transaction_read_only=1`、`CURRENT_ROLE()='NONE'`、可见权限仅 `SELECT/USAGE`。应用代码不会 `SET` 会话变量或修改账号。若现有 MySQL 环境无法证明这些条件，应停止并重新设计读取通道。

## 固定 QueryId、表和字段

全部 SQL 是 `scripts/r3_db_read.py` 中不可变映射；调用方只传 `QueryId`，不能提交自由 SQL。最多 12 个 QueryId，业务查询前后各读一次以发现漂移；访问和权限查询前后也要复核。单次查询最多取 500 行，超限拒绝。

| QueryId | 表 / 字段范围 |
| --- | --- |
| `R3_ACCESS_BASELINE` | 当前库名、主体、角色、会话只读属性；不读业务表 |
| `R3_PRIVILEGES_BASELINE` | `information_schema.USER_PRIVILEGES` / `SCHEMA_PRIVILEGES` / `TABLE_PRIVILEGES` / `COLUMN_PRIVILEGES` 的 `GRANTEE, PRIVILEGE_TYPE` |
| `R3_RULE_VERSION_BASELINE` | `learning_plan_credit_rule_versions`: `id, plan_key, version_label, status, based_on_version_label`，固定版本 |
| `R3_RULE_BASELINE` | `learning_plan_credit_rules`: `id, rule_version_id, course_key, course_name, year_index, credit_points, status, source, aliases_json`，仅固定版本规则 |
| `R3_GENERIC_VERSION_BASELINE` | `learning_credit_rule_versions`: `id, rule_set_key, version_label, status`，固定版本 |
| `R3_GENERIC_RULE_BASELINE` | `learning_credit_rules`: `rule_version_id, rule_key, settlement_model, points, cap_points, status`，仅固定版本 |
| `R3_MAPPING_BASELINE` | `learning_plan_credit_rule_mappings`: `plan_key, plan_version_label`；仅汇总 COUNT |
| `R3_BINDING_BASELINE` | `class_learning_bindings`: `status, plan_version_id, credit_rule_version_id, course_credit_rule_version_id`；`learning_plan_versions`: `id, plan_key, version_label`；仅汇总 COUNT/SUM |
| `R3_LEDGER_BASELINE` | `learning_credit_entries`: 仅 `points` 汇总及 COUNT，不读学员明细 |
| `R3_MIGRATION_BASELINE` | `schema_migrations`: `version`，只查 0064/0065/0066 的固定文件名 |
| `R3_APPLY_AUDIT_BASELINE` | `audit_logs`: `action`，仅固定 APPLY action 的 COUNT |
| `R3_PLACEHOLDER_BASELINE` | `study_meeting_sessions(course_key)`、`study_meeting_courses(id, course_key)`、`study_meeting_course_completions(study_meeting_course_id)`、`learning_credit_entries(rule_key)`、`learning_plan_credit_rule_mappings(generic_rule_version_id, course_credit_rule_version_id)`、`learning_credit_rule_versions(id, rule_set_key, version_label)`、`learning_plan_credit_rule_versions(id, plan_key, version_label)`；仅固定 placeholder 的引用 COUNT |

连接/权限验真需要的元数据读取与业务表授权分别审查。账本本轮只允许聚合，不允许取 `member_id` 或交易级明细。原始行不进 bundle，仅保存派生数量和查询结果指纹。

## 待签发字段与请求边界

| 字段 | 当前值 |
| --- | --- |
| 环境/地域 | 生产；`ap-shanghai` |
| 数据库实例与库名 | `UNVERIFIED / TO_BE_FIXED` |
| 访问通道和允许来源 | `UNVERIFIED / TO_BE_FIXED` |
| 只读主体、权限证明 | `UNVERIFIED / TO_BE_FIXED` |
| 运行时间窗口、操作者 | `NOT_AUTHORIZED / TO_BE_FIXED` |
| 固定查询集合 | 上表 12 个 QueryId；不可增补 |
| 最大业务写次数 | `0` |
| 最大查询批次 | 一批；异常不自动重试 |
| 预期结果 | 新鲜且前后稳定的 `DatabaseBaselineEvidence`，不预设数量/指纹 |

授权方需要单独批准“由固定主体经固定通道，在固定时间窗口，对固定库执行上述 12 个 QueryId 的一批只读 SELECT”。这**不**授权任意 SQL、`INSERT/UPDATE/DELETE`、DDL、备份/恢复、迁移、R3 APPLY、0064/65/66、历史 staging 或 ledger POST。

执行前需有受审计的生产连接工厂和专项测试；当前代码没有真实 DB CLI 入口。任一账号权限过宽、目标不符、字段缺失、读前后变化、超时或证据签封失败即失败关闭；不自行加权限、改变 SQL 或切换生产账号。只有控制面、runtime、DB 三类证据都 PASS，才有资格申请后续生产动作，仍不能自动执行。
