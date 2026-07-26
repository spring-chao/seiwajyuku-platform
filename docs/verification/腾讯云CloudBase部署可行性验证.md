# 腾讯云 CloudBase 部署与验证基线

更新时间：2026-07-27
环境 ID：`shengheshu-d2g2zyyl99f6c6fc2`

## 当前结论

本平台已在与签到系统共用的 CloudBase 环境中完成独立部署：

- Vue 管理门户部署在静态托管的 `/ops-platform/` 目录。
- FastAPI 部署为独立 CloudRun 服务 `seiwajyuku-platform-api`。
- 平台使用独立 MySQL 数据库和独立环境变量。
- 登录、年度 MP 看板、月度填报、学员管理及关怀跟进 API 已由完整后端提供。
- CloudRun 服务升级不会修改签到系统的云函数、HTTP 路径或静态页面。

## 在线资源与验证结果

| 验证项 | 地址或资源 | 当前结果 |
| --- | --- | --- |
| 管理门户 | `https://shengheshu-d2g2zyyl99f6c6fc2-1453587887.tcloudbaseapp.com/ops-platform/` | HTTP 200 |
| CloudRun 服务 | `seiwajyuku-platform-api` | 运行中 |
| API 根地址 | `https://seiwajyuku-platform-api-287369-8-1453587887.sh.run.tcloudbase.com` | 可访问 |
| API 健康检查 | `/api/v1/health` | HTTP 200 |
| 责任人接口 | `/api/v1/followups/assignees` | 未登录返回 HTTP 401，证明路由存在且鉴权生效 |
| 前端静态资源 | `ops-platform/` | HTTP 200 |

`401` 是未携带登录令牌时的预期响应；若新接口返回 `404`，应视为后端版本未正确发布。

## 与签到系统的隔离

本平台不得修改或删除以下签到系统资源：

- 云函数：`checkinApi`
- HTTP 访问路径：`/api`、`/api/*`
- 静态托管根目录及签到系统已有页面

本平台使用的独立资源为：

- 静态目录：`ops-platform/`
- CloudRun 服务：`seiwajyuku-platform-api`
- 独立 MySQL 数据库及平台专用环境变量

早期只读预览函数 `seiwajyukuPlatformApiPreview` 和 `/ops-preview` 不是当前完整 API 的发布目标。

## 发布流程

1. 确认工作区只包含本次发布内容，并记录当前 Git 提交号。
2. 运行后端测试、前端类型检查和预发布构建。
3. 从仓库根目录发布 CloudRun 服务：

   ```powershell
   tcb cloudrun deploy --serviceName seiwajyuku-platform-api --port 8000 --source . --force --env-id shengheshu-d2g2zyyl99f6c6fc2
   ```

4. 使用预发布环境变量构建前端，并将构建产物发布到 `ops-platform/`。
5. 发布完成后依次验证健康检查、登录、关键页面及本次新增 API。

发布命令不得包含真实数据库密码、令牌或管理员密码；敏感配置只通过 CloudRun 环境变量管理。

## 发布前验证门禁

- 后端测试通过。
- Vue 和 TypeScript 类型检查通过。
- 前端预发布构建通过。
- `/api/v1/health` 返回 HTTP 200。
- 登录可正常取得令牌，错误密码有明确提示。
- 年度 MP 看板和月度填报的数据、单位、百分比及分中心范围正确。
- 新增 API 未登录时返回 `401` 或 `403`，不得返回 `404` 或 `500`。
- 关键静态资源返回 HTTP 200，浏览器控制台无阻断性错误。

## 回滚策略

发生阻断性问题时，不删除 CloudBase 环境、签到系统资源或共享静态根目录：

1. 在 CloudRun 版本管理中将流量切回上一稳定版本，或从上一稳定 Git 提交重新部署。
2. 将 `ops-platform/` 恢复为上一稳定构建产物。
3. 若涉及数据库迁移，先停止写入，再按对应迁移文档执行反向迁移或从已验证备份恢复。
4. 回滚后重新检查健康接口、登录和签到系统既有入口。

任何删除资源的操作都不属于常规回滚流程，必须另行确认精确资源名称和影响范围。
