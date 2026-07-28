# 任务卡：P0 安全修复 + 班级学习会三次签到积分设计实施

## 任务目标

基于代码级审核结论，修复 6 项 P0 问题并实现班级学习会三次签到（上午7分/下午7分/空巴4分）个人积分模型。

## 业务依据

- 需求规格说明书 V1.1：手机号安全、组织隔离、签到参与率
- 技术设计 V1.1：IAM、年度MP、集成快照、迁移边界
- 代码级审核报告（2026-07-28）：6 项 P0 问题 + 三次签到设计方案

## 审核结论

| 维度 | 代码级评价 |
|------|-------:|
| 技术架构 | 88% |
| 年度MP模型 | 78% |
| 组织与权限 | 62% |
| 数据安全 | 68% |
| 关怀和企业走访 | 75% |
| 续费运营 | 45% |
| 现有签到汇总整合 | 60% |
| 个人三次签到积分 | 10% |
| 测试和发布门禁 | 58% |
| 生产可用性 | 40% |
| **综合代码完成度** | **约65%** |
| **综合建设方向一致性** | **约82%** |

## P0 问题修复状态

### P0-1：完整手机号存在绕过任务用途控制的路径 — 已修复并收紧

**问题**：`GET /api/v1/members/{id}/detail` 直接返回解密手机号和企业财务数据，`operations_admin` 和 `regional_manager` 可无条件调用，绕过了 `contact:reveal` 的逐人任务机制。

**修复**：
- `members:detail_view` 仅返回脱敏基本资料，区域、班级和小组角色仍可按组织关系查看
- `members.py`：`get_member_detail()` 改为返回 `phone_masked`，不再返回企业财务数据
- 新增 `get_member_enterprise_detail()`：需填写用途+审计，仅 `operations_admin`/`system_admin`，且不返回完整手机号
- 新增 API `POST /api/v1/members/{id}/enterprise-detail`
- 新增权限 `members:enterprise_view`（RESTRICTED）
- 完整手机号仍仅通过有效联系任务或经审计的签到系统名单匹配接口临时返回
- 更新 `test_privacy.py` 验证新安全模型

### P0-2：MP导入存在组织范围穿透 — 已修复

**问题**：`regional_manager` 拥有 `plans:write`，`apply_preview()` 不检查操作者组织范围，可覆盖其他分中心数据。

**修复**：
- 权限拆分：`plans:write` → `plans:period_write` + `plans:import_global` + `plans:publish`
- `api/imports.py`：导入端点改用 `plans:import_global`（仅 `operations_admin`）
- `api/plans.py`：填报端点改用 `plans:period_write`
- `mp_import.py`：`apply_preview()` 增加防御性 org scope 检查

### P0-3：班级和小组权限缺少正式关系模型 — 代码闭环，数据回填待执行

**问题**：`members` 表仅保存 `class_name`/`group_name` 文本，班主任无法按班级查看学员。

**修复**：
- 新增迁移 `0008_member_org_relations.sql`（sqlite + mysql）
- 表 `member_org_relations`：支持 PRIMARY_REGION、STUDY_CLASS、STUDY_GROUP、SPECIAL_COHORT、DEVELOPMENT_RELATION
- 新增 `services/checkin_rosters.py`：基于关系表的名单服务
- 学长列表、详情和个人积分权限已接入正式关系表
- **部署前置**：将现有 `class_name`/`group_name` 文本映射回填到关系表

### P0-4：生产发布镜像、迁移和备份链路未闭合 — 部分修复

**问题**：CI 构建使用 `apps/platform-api/Dockerfile`（缺少 `COPY migrations`），与根 Dockerfile 不一致。

**修复**：
- 删除冗余 `apps/platform-api/Dockerfile`
- CI 改用根 Dockerfile（`docker build -t ... .`）
- **待办**：生产 MySQL 备份恢复脚本、迁移测试

### P0-5：两个系统的名单接口契约未落地 — 已修复

**问题**：签到系统调用 `GET /api/v1/checkin-rosters/options` 和 `GET /api/v1/checkin-rosters/members`，但运营平台未实现。`settings.py` 未读取 `SIGNIN_API_BASE_URL`/`SIGNIN_SERVICE_API_KEY`。

**修复**：
- 新增 `api/checkin_rosters.py`：`GET /options` + `GET /members`（X-API-Key 认证）
- `settings.py`：新增 `signin_api_base_url` + `signin_service_api_key` 配置
- `main.py`：注册 `checkin_rosters_router`
- 机器接口返回完整手机号（签到匹配用），限制单次班级/小组范围

### P0-6：签到重复名额机制不适合个人积分 — 已修复

**问题**：签到时按 `remainingIndexes.slice(0, quantity)` 选择前N条记录，不区分不同 `member_code`。

**修复**：
- 签到系统新增 `/create_class_meeting_sessions`：一键创建三场次
- 事件新增 `event_group_id`、`session_code`、`scheduled_start_at`、`scheduled_end_at`
- 名单保存 `member_code`
- 新增增量拉取接口供运营平台同步
- 同步端按 `member_code` 解析 `member_id`，未匹配记录标记为 `UNMATCHED`
- 活动类型统一为 `CLASS_MEETING`，支持安全重算
- 人工裁定已实现早退撤销、补签、作废、请假撤销和人员重关联

## 三次签到积分设计

### 积分规则

| 场次 | session_code | 基础分 | 迟到扣分 | 早退扣分 |
|------|-------------|-------|---------|---------|
| 上午 | MORNING | 7 | 1 | 1 |
| 下午 | AFTERNOON | 7 | 1 | 1 |
| 空巴 | KONPA | 4 | 1 | 1 |

### 迟到判定

```
checked_at > scheduled_start_at → 迟到
```

使用云函数服务器时间，不使用客户端时间。

### 早退判定

不由下一场未签到反推。必须人工裁定（`attendance_adjudications` 表）。

### 数据库新增表

| 表 | 用途 |
|----|------|
| attendance_event_groups | 班级学习会逻辑活动 |
| attendance_sessions | 上午/下午/空巴签到场次 |
| attendance_records | 每人每场次一条出勤记录 |
| attendance_score_rules | 可版本化积分规则 |
| attendance_score_records | 积分记录（可重算） |
| attendance_adjudications | 人工裁定（早退/补签/作废） |
| attendance_sync_runs | 增量同步运行记录 |

### 同步方向

运营平台主动从签到系统增量拉取（非签到系统推送积分）。

## 允许修改

- 仓库/目录：`seiwajyuku-platform/apps/platform-api/`、`scripts/checkin_v2/`
- 数据库表：新增 0008、0009 迁移
- 外部系统：签到系统 cloudfunc/index.js

## 禁止事项

- 不连接或修改生产数据
- 不泄露密钥或敏感个人数据
- 不扩大任务范围

## 交付物

### 核心修改文件

| 文件 | 变更 |
|------|------|
| `app/services/iam.py` | 权限拆分，新增 enterprise_view/attendance:* |
| `app/services/members.py` | detail 返回脱敏，新增 enterprise_detail |
| `app/services/mp_import.py` | apply_preview 增加 org scope 检查 |
| `app/core/settings.py` | 读取 SIGNIN_API_BASE_URL/SIGNIN_SERVICE_API_KEY |
| `app/api/members.py` | 新增 enterprise-detail + attendance-scores 端点 |
| `app/api/imports.py` | plans:import_global |
| `app/api/plans.py` | plans:period_write |
| `app/main.py` | 注册 checkin_rosters + attendance 路由 |
| `tests/test_privacy.py` | 验证新安全模型 |
| `tests/test_iam.py` | 验证班级关系授权 |
| `tests/test_settings.py` | 覆盖签到配置字段 |

### 核心新增文件

| 文件 | 用途 |
|------|------|
| `app/services/checkin_rosters.py` | 名单服务（基于 member_org_relations） |
| `app/services/attendance_scoring.py` | 积分计算/重算/个人明细 |
| `app/services/attendance_sync.py` | 增量拉取同步 |
| `app/api/checkin_rosters.py` | 签到名单 API |
| `app/api/attendance.py` | 出勤管理 API |
| `migrations/sqlite/0008_member_org_relations.sql` | 多关系模型 |
| `migrations/mysql/0008_member_org_relations.sql` | 多关系模型 |
| `migrations/sqlite/0009_attendance_scoring.sql` | 个人出勤积分 |
| `migrations/mysql/0009_attendance_scoring.sql` | 个人出勤积分 |
| `tests/test_attendance_scoring.py` | 人员匹配、积分、裁定和分页测试 |
| `scripts/backfill_member_org_relations.py` | 历史关系预览/回填，默认只读且禁止生产执行 |
| `migrations/rollback/{sqlite,mysql}/0008*.down.sql` | 关系模型回滚 |
| `migrations/rollback/{sqlite,mysql}/0009*.down.sql` | 出勤积分模型回滚 |

### 签到系统修改（1个）

| 文件 | 变更 |
|------|------|
| `scripts/checkin_v2/cloudfunc/index.js` | 三场次创建、增量拉取、member_code、publicEvent 新字段 |

### CI 修改（1个）

| 文件 | 变更 |
|------|------|
| `.github/workflows/ci.yml` | 统一使用根 Dockerfile |

## 测试要求

- Python：`compileall` 语法验证 ✅
- 集成：签到系统 JS 通过 `node --check` ✅
- 权限：`test_privacy.py` 已更新验证脱敏+企业资料分离
- 隐私：企业资料接口不返回完整手机号；完整手机号仅在 contact-access（任务+用途）和经审计的签到名单匹配中返回
- 自动测试：本地全新 SQLite 测试库 `pytest 31 passed`；CI 已统一执行完整 pytest 套件 ✅
- 数据核对：SQLite 0008/0009 前向迁移通过；MySQL 真实实例迁移仍待 staging 验证

## 回滚说明

- 迁移 0008/0009 为纯新增表；已提供 SQLite/MySQL 对应 `.down.sql`
- `seed_iam()` 会按当前定义重建系统角色权限映射，清除历史残留授权
- 签到系统新端点为增量添加，不影响现有签到流程
- Dockerfile 统一后 CI 回滚仅需还原 `ci.yml` 一行

## 待后续工作

1. 前端页面：活动与出勤（班级学习会、出勤明细、异常处理、个人积分、同步记录）
2. 先运行 `scripts/backfill_member_org_relations.py` 预览，再在 staging 显式确认执行回填并处理无法匹配清单
3. 黄埔班组织类型修正：从 CLASS 改为 SPECIAL_COHORT
4. metric_overrides 表启用：人工覆盖写入原因和自动原值
5. 生产 MySQL 备份恢复脚本和演练
6. 两个系统正式联调：配置密钥、并行核对、回滚演练
7. 续费运营业务闭环：应用导入、生成周期、逐人工作台
