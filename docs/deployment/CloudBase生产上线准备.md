# CloudBase 生产上线准备

更新时间：2026-07-26

## 推荐方案

完整 API 推荐采用 CloudBase Run 容器服务，持久数据采用同环境独立 CloudBase MySQL。原因是：

- FastAPI 及加密、Excel、MySQL 驱动依赖由容器镜像固定，避免云函数在线安装依赖不完整。
- CloudBase Run 可以无流量缩容到 0，适合当前低流量阶段。
- CloudBase MySQL 支持标准 SQL、自动暂停和 VPC 内网连接。

## 当前只读清点

- 环境：`shengheshu-d2g2zyyl99f6c6fc2`，个人版。
- 云托管服务数量：0，尚未开通。
- CLI 账户级列表可见一台 Serverless MySQL：
  - 实例：`cynosdbmysql-ins-1zjc0ifl`
  - MySQL 5.7，上海二区，0.25～0.5 CCU。
  - 当前为冻结、自动暂停状态。
  - 对目标环境查询备份时，CloudBase 返回环境关联不存在。
  - 使用另一个环境 `seiwajyuku-ops-prod-d4b0772e4af9` 查询会返回同一实例；该环境另有 `seiwajyuku-ops` 云托管和签到同步函数。

因此将该数据库实例视为其他项目资源，明确排除。不得唤醒、变配、连接或执行迁移；本平台需要在目标环境内另行初始化独立 MySQL。

## 本轮已完成的无费用准备

- 生产容器改为非 root 用户运行。
- 增加容器级 `/health/live` 健康检查。
- `.dockerignore` 排除测试、数据库、环境变量和云函数预览文件。
- CI 增加生产 API 镜像构建。
- 生产环境允许以 `DEPLOYMENT_READ_ONLY=true` 启动，但会跳过迁移和 IAM 初始化。
- 未授权生产写入时，所有非只读 HTTP 方法继续返回 403。

## 需要用户确认的资源动作

以下动作可能产生费用或影响现有资源，执行前必须获得明确批准：

1. 在目标环境初始化本平台独立的 CloudBase MySQL。
2. 开通 CloudBase Run 服务 `seiwajyuku-platform-api`。
3. 配置 VPC、数据库账号、数据库名称和应用密钥。
4. 首次执行 MySQL 迁移和 IAM 初始化。
5. 将新 API 路由切换到正式服务并开放业务写入。

## 建议上线顺序

1. 先建立独立预发布数据库及最小权限账号。
2. 以 `APP_ENV=production`、`DEPLOYMENT_READ_ONLY=true` 部署容器，验证 `/health/live`。
3. 完成数据库备份后，在单独获批的迁移任务中临时启用生产变更门禁并执行迁移。
4. 切回只读模式，验证 `/health`、IAM 和数据范围。
5. 最终批准后再关闭只读模式并开放写入流量。

不得把数据库口令、JWT 密钥、字段加密密钥或管理员初始口令写入仓库。
