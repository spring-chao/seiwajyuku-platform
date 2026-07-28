# 任务 2：dev/staging、备份恢复与 CI

## 交付

- dev：SQLite、本地 `api-dev` 容器、默认禁止生产写入；
- staging：独立 MySQL 容器和独立配置示例；
- 配置：根目录三份 `.env*.example`，不包含真实密钥；
- 恢复门禁：脚本拒绝 production，恢复必须显式确认当前环境且校验 SHA-256；
- CI：API 测试、pure-admin-thin 类型检查、staging 构建、生产镜像构建，以及隔离 MySQL 8.4 迁移/回填/同步/积分/备份恢复验证。

## 本地验收

```powershell
Copy-Item .env.dev.example .env
docker compose --profile dev up --build
Invoke-RestMethod http://localhost:8000/health
```

预期 `status=ok`。不得把 `.env`、`data/*.db` 或 `backups/` 提交到 Git。

## staging 验收

1. 从 `.env.staging.example` 创建独立 `.env`；
2. 替换全部占位密码和密钥；
3. 运行 `docker compose --profile staging up --build`（使用根上下文 `Dockerfile.staging`，包含 migrations）；
4. 访问 `http://localhost:8001/health`；
5. 确认未配置任何生产数据库地址或生产敏感数据。

GitHub Actions 的 `mysql-staging` 作业会为每次运行创建并销毁独立 MySQL 8.4 容器，禁止远程数据库地址，自动执行 0001-0009 迁移、合成数据回填、三场签到同步、积分核对、`mysqldump` 和空库恢复。运行 `30361733821` 已完整通过；这用于数据库兼容和恢复门禁，不替代长期共享的 staging 服务。

## 回滚

- 停止容器不会修改生产环境；
- dev 数据可从 `scripts/backup_restore.py` 生成的备份恢复；
- staging 回滚先恢复独立 staging 备份，再回退对应 Git 提交；
- 未获批准不得将本流程用于生产。
