# 盛和塾综合运营与发展建设平台

面向盛和塾运营团队的统一管理门户，承载年度 MP、月度填报、学员管理、关怀跟进、活动签到及后续系统整合。前端基于 `pure-admin/pure-admin-thin` 改造，后端采用 FastAPI 模块化单体架构。

## 当前交付状态

- 年度 MP 看板：已完成数据导入、分中心展示、预定达成率与年度目标达成率。
- 月度填报：已按分中心拆分，百分比和数值统一取整展示。
- 学员管理：已扩展运营管理所需的学员、企业、班组和推荐关系字段。
- 关怀跟进：已恢复责任人接口和选择流程，支持创建关怀任务。
- 权限与隐私：已实现登录、角色范围隔离、手机号脱敏及敏感字段保护。
- 活动与签到：已预留和现有签到系统的独立集成边界。

年度 MP 看板和月度填报已通过当前业务确认；学员管理、关怀跟进及外部系统整合仍需继续业务验收。

## 技术架构

```text
apps/
  admin-web/       Vue 3 + TypeScript + pure-admin-thin 管理门户
  platform-api/    FastAPI 业务 API
packages/
  api-contracts/   共享接口与枚举
  metric-definitions/
migrations/        可回滚数据库迁移
scripts/           环境、导入、备份、恢复和核对脚本
tests/             API、权限、隐私、数据与集成测试
docs/              基线、任务卡、验收和部署说明
Codex记忆/         项目决策、交付基线和未完成事项
```

## 开发基线

- 业务基线：`docs/baseline/需求规格说明书_V1.1_最终开发基线.docx`
- 技术基线：`docs/baseline/技术设计与Codex开发实施文档_V1.1.docx`
- 首批任务：`docs/tasks/首批8个Codex任务.md`

## 本地启动

```powershell
Copy-Item .env.example .env
docker compose --profile dev up --build
```

仓库只提交环境变量示例。实际数据库凭据、密钥和管理员密码必须通过本地环境文件或云端环境变量注入。

## 腾讯云部署

- CloudBase 环境：`shengheshu-d2g2zyyl99f6c6fc2`
- 管理门户：`https://shengheshu-d2g2zyyl99f6c6fc2-1453587887.tcloudbaseapp.com/ops-platform/`
- CloudRun API：`https://seiwajyuku-platform-api-287369-8-1453587887.sh.run.tcloudbase.com`
- 健康检查：`https://seiwajyuku-platform-api-287369-8-1453587887.sh.run.tcloudbase.com/api/v1/health`

平台与签到系统共用 CloudBase 环境，但使用独立静态目录、CloudRun 服务和数据库，不修改签到系统的 `checkinApi`、`/api` 路径和既有页面。部署边界、验证门禁及回滚策略见 `docs/verification/腾讯云CloudBase部署可行性验证.md`。

## 安全与交付门禁

1. 本地开发只使用脱敏样例数据。
2. 禁止提交 `.env`、数据库备份、访问令牌、完整手机号或精确企业敏感数据。
3. 数据库迁移和业务写入变更必须先备份、验证回滚并取得业务确认。
4. 每次发布必须检查健康接口、登录、关键业务页面和新增 API 路由。
5. 发布失败优先回滚到上一稳定版本，不删除与签到系统共享的环境资源。

`apps/admin-web` 基于 MIT 许可的 pure-admin-thin 改造，其上游许可文件保留在该目录中。本仓库其余业务代码和资料为内部项目资产，未经授权不得公开传播。
