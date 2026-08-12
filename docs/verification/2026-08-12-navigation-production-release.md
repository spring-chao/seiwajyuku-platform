# 导航信息架构生产发布核验（2026-08-12）

## 发布结论

管理门户的导航信息架构已正式发布。原“运营驾驶舱”下混合的年度规划、学员运营、活动签到与身份任职，现按业务职责分为四个一级菜单；原有页面地址和页面级权限不变。

| 一级菜单 | 二级页面 |
| --- | --- |
| 运营驾驶舱 | 年度 MP 看板、月度填报 |
| 学员运营 | 学员管理、关怀跟进、续费运营 |
| 活动管理 | 活动与签到 |
| 系统设置 | 身份与任职 |

## 发布内容

- 代码提交：`174b9a8 feat: reorganize operations navigation`；
- 管理门户以 staging 配置构建，公开路径为 `/ops-platform/`，继续连接既有生产 API；
- 静态资源已发布到 CloudBase Hosting 的 `ops-platform/` 目录，41 个文件全部上传成功；
- 本次未发布 API、未执行数据库迁移，也未调用业务写入接口。

## 线上核验

- 管理门户入口返回 HTTP 200；
- 当前首页引用的新主资源 `index-6EueHLGH.js` 返回 HTTP 200；
- 已发布资源包含 `MemberOperations`、`ActivityManagement`、`SystemSettings` 三个新分组；
- API `/api/v1/health` 返回 `status=ok`；
- 未登录请求续费周期接口继续返回 HTTP 401；
- 生产环境实时状态：`production=true`、`deployment_read_only=false`、`production_mutations_allowed=true`、`identity_admin_writes_enabled=false`。

## 兼容与后续确认

- 页面访问地址保持不变：`/operations/dashboard`、`/operations/mp-entry`、`/operations/members`、`/operations/followups`、`/operations/renewals`、`/operations/activities`、`/operations/identity-admin`；
- 既有页面角色限制保持不变；身份与任职仍仅允许平台管理员和技术管理员访问；
- 尚未用真实运营账号进行登录后菜单视觉验收。运营人员下次登录时请确认左侧菜单依次显示四个分组及对应页面；如发现问题，可将 Hosting 的 `ops-platform/` 静态文件恢复为上一稳定构建，本次无需 API 或数据库回退。
