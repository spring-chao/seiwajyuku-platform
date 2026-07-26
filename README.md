# 盛和塾综合运营与发展建设平台

本仓库承载统一管理门户、统一业务 API、年度 MP、发展建设与现有签到/读书系统的渐进式整合。

## 开发基线

- 业务基线：`docs/baseline/需求规格说明书_V1.1_最终开发基线.docx`
- 技术基线：`docs/baseline/技术设计与Codex开发实施文档_V1.1.docx`
- 首批任务：`docs/tasks/首批8个Codex任务.md`

## 仓库结构

```text
apps/
  admin-web/       Vue 3 + TypeScript 统一管理门户
  platform-api/    FastAPI 模块化单体业务 API
packages/
  api-contracts/   共享接口与枚举
  metric-definitions/
migrations/        可回滚数据库迁移
scripts/           环境、导入、备份、恢复和核对脚本
tests/             API、权限、隐私、数据与集成测试
docs/              基线、任务卡、验收和回滚说明
```

## 强制门禁

1. 本地开发只使用脱敏样例数据。
2. `staging` 必须使用独立数据库和独立密钥。
3. 未收到业务负责人明确的“批准上线”，不得连接生产数据库、执行生产迁移或生产部署。
4. 禁止提交 `.env`、数据库备份、访问令牌、完整手机号或精确企业敏感数据。
5. 每个任务必须附测试结果、业务验收步骤、费用影响和回滚说明。

## 本地启动

```powershell
Copy-Item .env.example .env
docker compose --profile dev up --build
```

## 腾讯云只读验证环境

- 管理门户：`https://shengheshu-d2g2zyyl99f6c6fc2-1453587887.tcloudbaseapp.com/ops-platform/`
- API 健康探针：`https://shengheshu-d2g2zyyl99f6c6fc2-1453587887.ap-shanghai.app.tcloudbase.com/ops-preview/api/v1/health`
- 当前状态：只读可行性验证。前端静态托管及独立 API 路径可用，完整 API 因持久数据库和 Python 运行依赖尚未配置而未启用。

详细边界、验证证据与回滚命令见 `docs/verification/腾讯云CloudBase部署可行性验证.md`。
