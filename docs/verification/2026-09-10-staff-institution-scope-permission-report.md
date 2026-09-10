# 专职人员“所属机构—负责范围—岗位—权限”重整报告

日期：2026-09-10
范围：IAM2 专职人员管理、机构/组织范围联动、管理端菜单权限判断
边界：未修改 Volunteer 2.0 授权模型，未写入真实志工数据。

## 1. 生产只读核查

本轮先通过 CloudBase 生产数据库只读查询核对机构与组织主数据。以下是 `0056` 主数据补齐迁移执行前的生产快照，不代表补齐后的最终生产状态：

### operating_institutions

| id | code | 名称 | 类型 | 上级 | 状态 |
| --- | --- | --- | --- | --- | --- |
| institution-seiwa-hq | SEIWA_HQ | 盛和塾总部 | HEADQUARTERS | — | 启用 |
| institution-jiangnan | JIANGNAN | 江南塾 | DIVISION | 盛和塾总部 | 启用 |
| institution-suzhou | SUZHOU_CENTER | 苏州分中心 | CITY_CENTER | 江南塾 | 启用 |
| institution-suzhou-operations | SUZHOU_OPERATIONS_CENTER | 苏州分中心运营中心 | OPERATIONS_CENTER | 苏州分中心 | 启用 |

该快照中常州塾、无锡塾尚没有生产机构记录；当时不能伪造机构或组织树。业务随后确认了常州七个分中心，因此本报告已由“缺口待确认”修正为“已确认、待执行主数据迁移”。

### institution_org_links / org_units

- 当前正式机构—组织映射只有：`SUZHOU_CENTER` → `SZ_ROOT`（苏州塾根节点）。
- 生产 `org_units` 只有一个 ROOT：苏州塾。
- 生产组织统计：REGIONAL_CENTER 6（启用 6）、CLASS 26（启用 24）、GROUP 130（启用 127）。
- 因此当时江南、常州、无锡的组织根和下级树属于“主数据未落地”，不是前端筛选遗漏。

## 2. 业务确认后的主数据补齐方案

业务已确认江南多塾结构，不再重新设计：

```text
江南塾
├─ 苏州塾（保留现有 org-suzhou / SZ_ROOT 及全部下级 ID）
├─ 常州塾
│  ├─ 天宁分中心
│  ├─ 武进分中心
│  ├─ 钟楼分中心
│  ├─ 经开分中心
│  ├─ 新北分中心
│  ├─ 健行分中心
│  └─ 龙城分中心
└─ 无锡塾（本轮只落塾级根，不猜下级）
```

已新增 `0056_jiangnan_org_master_data.sql`（SQLite/MySQL）：

- `JN_ROOT` / `org-jiangnan`：江南塾根节点；
- `CZ_ROOT` / `org-changzhou`：常州塾根节点；
- `CZ_TIAN_NING`、`CZ_WU_JIN`、`CZ_ZHONG_LOU`、`CZ_JING_KAI`、`CZ_XIN_BEI`、`CZ_JIAN_XING`、`CZ_LONG_CHENG`：常州七个正式分中心；
- `WX_ROOT` / `org-wuxi`：无锡塾根节点，暂不创建未确认的下级；
- `institution-changzhou` / `CHANGZHOU_CENTER`、`institution-wuxi` / `WUXI_CENTER`：正式专职机构；
- `institution_org_links` 统一使用稳定 ID 和 `SERVICE_BOUNDARY`；不按名称匹配；
- 苏州既有 `org-suzhou`、`SZ_ROOT` 及其下级节点不重建、不换 ID，只补到江南根下；
- 回滚脚本会在新组织已有业务引用时拒绝删除。

## 3. 已完成的代码调整

### 机构与范围

- 普通新增/编辑目录改用稳定机构编码配置，只展示正式业务机构；总部、运营中心和其他历史机构不再进入普通机构下拉框。
- `institution_org_links` 是机构到组织根的唯一映射来源，不按中文名称猜测。
- 兼容旧的苏州运营中心记录读取：沿用上级苏州机构的正式根映射，但不再作为普通新增/编辑的写入选项。
- 负责范围按所选机构根节点递归过滤；保存时后端再次校验，越过机构树或操作者自身授权范围会被拒绝。
- 机构或组织根尚未落地时，目录返回 `missing_institutions`/`scope_available=false`，页面明确提示补齐主数据，不产生虚假组织。
- 范围类型继续只显示“含下级 / 仅本级”，不向运营人员暴露 `SUBTREE` / `UNIT`。

### 岗位与权限

- 岗位目录增加短职责说明和目标 IAM2 role 映射，页面不显示 permission key。
- `ops_center_director` 继续使用独立 `employee_operations_lead` role key，但其业务权限模板与 `operations_admin` 对齐；不包含技术超级管理员能力。
- 新增独立 `staff:manage` 权限。普通业务负责人可维护普通专职人员；`iam:manage` 仍是技术 IAM 管理能力。旧技术管理员入口保留兼容。
- 新增独立 `enrollment:unassigned_review`，仅业务 Admin/运营中心负责人可查看未分配组织的入塾申请，避免把区域审核权限误扩大。
- `ops_center_operations`、`ops_center_learning`、`ops_center_development`、`ops_center_management`、`ops_center_data`、`ops_center_finance`、`ops_center_administration` 保持独立岗位职责和权限模板。

### 菜单与接口

- 管理端业务菜单从 role-name 白名单改为 permission-driven；路由守卫和菜单过滤同时支持 `meta.auths`。
- 专职人员接口改为 `staff:manage` / `iam:manage` 兼容入口，并按当前 permission 的组织范围解析器执行读写。
- 入塾申请的未分配数据判断改为独立 permission，不再直接比较 `system_admin` / `operations_admin` 角色名称。

## 4. 目标权限矩阵

| 岗位 | 目标 role key | 主要业务能力 | 范围约束 |
| --- | --- | --- | --- |
| 运营中心负责人 | `employee_operations_lead` | 业务 Admin：学员、关爱、续费、活动学习、入塾、签到、数据、组织运营、普通导出、专职人员维护 | 只在负责范围内 |
| 运营专员 | `employee_operations_management` | 学员、关爱、续费及日常运营 | 只在负责范围内 |
| 学习践行专员 | `employee_learning_management` | 学习计划、课程、学习会、出勤 | 只在负责范围内 |
| 发展建设专员 | `employee_development_management` | 新学长、入塾审核、发展跟进 | 只在负责范围内 |
| 运营管理专员 | `employee_operations_management` | 年度计划、规则、运营组织与统计 | 只在负责范围内 |
| 数据专员 | `employee_data_management` | 数据、导入导出、签到同步、数据维护 | 只在负责范围内 |
| 财务专员 | `employee_finance_management` | 续费、收款确认 | 只在负责范围内 |
| 行政专员 | `employee_administration_management` | 基础资料、行政支持、相关关爱 | 只在负责范围内 |

岗位决定“能做什么”，负责范围决定“在哪里做”，所属机构只表示组织归属。

## 5. 验证结果

- 后端完整回归：`564 passed, 2 skipped`。
- IAM2 专项回归：`17 passed`，覆盖机构目录、职责说明、运营负责人权限一致性、范围越权拒绝、密码 6 位规则和旧数据兼容。
- 江南组织主数据迁移专项：`2 passed`，覆盖苏州 ID 保留、常州七分中心、无锡只建根节点、机构映射和已使用数据回滚保护。
- 管理端 TypeScript/Vue 类型检查：通过。
- 管理端生产构建：通过（Vite 2133 modules）。
- 管理端静态权限检查：4 passed；确认业务菜单不再使用 `meta.roles`。
- 迁移前向/回滚契约：既有迁移测试通过；新增 0055 仅增加权限与稳定映射补偿，不创建常州/无锡虚假数据。

## 6. 当前未完成与发布边界

- 业务确认已经完成；当前待执行的是 `0056` 生产主数据迁移和对应版本发布。
- 本轮尚未执行生产部署、生产数据库迁移或生产权限写入；生产快照仍是第 1 节所列的补齐前状态。
- 发布后应重新核验四个正式塾的目录、江南/常州/苏州范围联动，以及无锡只有塾级根节点的预期，再进行真实工作人员验收。
