# 腾讯云 CloudBase 部署可行性验证

更新时间：2026-07-26  
环境 ID：`shengheshu-d2g2zyyl99f6c6fc2`

## 结论

在与签到系统共用 CloudBase 环境的前提下，使用独立静态目录、独立云函数和独立 HTTP 访问前缀部署本平台是可行的。当前已经完成管理门户与只读健康探针的在线部署，但这不是完整生产上线：

- Vue 管理门户可由静态托管的 `/ops-platform/` 独立目录提供。
- 平台 API 可由独立的 `/ops-preview` 前缀访问。
- 当前环境未开通云托管，CloudRun 部署请求被平台拒绝，未创建或开通付费资源。
- Python 云函数未自动安装 FastAPI 等完整运行依赖，且环境中尚无本平台持久数据库，因此函数只提供只读探针。
- 禁止把云函数 `/tmp` 下的 SQLite 当作生产数据库；登录、导入和业务写入均被明确阻止。

## 在线地址与验证结果

| 验证项 | 地址或资源 | 结果 |
| --- | --- | --- |
| 管理门户 | `https://shengheshu-d2g2zyyl99f6c6fc2-1453587887.tcloudbaseapp.com/ops-platform/` | HTTP 200 |
| API 健康探针 | `https://shengheshu-d2g2zyyl99f6c6fc2-1453587887.ap-shanghai.app.tcloudbase.com/ops-preview/api/v1/health` | HTTP 200，`full_api_available=false` |
| 写入保护 | `POST /ops-preview/api/v1/auth/login` | HTTP 403 |
| 静态资源 | `ops-platform/` | 35 个文件独立部署 |
| 平台函数 | `seiwajyukuPlatformApiPreview` | Python 3.10，部署完成 |
| 平台访问服务 | `/ops-preview` | 仅绑定平台预览函数 |

## 本地质量验证

- 后端：`15 passed`；覆盖系统健康检查、IAM 范围隔离、MP 只读导入、隐私导出、关怀与企业走访闭环、签到/读书集成及 CloudBase 只读适配器。
- 前端：TypeScript 与 Vue 类型检查通过。
- 前端预发布构建：2,035 个模块构建成功，产物约 2.33 MB。

## 与签到系统的隔离

部署前后均未修改签到系统的以下资源：

- 云函数：`checkinApi`
- HTTP 访问路径：`/api`、`/api/*`
- 静态托管根目录及签到系统已有页面

本平台新增资源仅为：

- 静态目录：`ops-platform/`
- 云函数：`seiwajyukuPlatformApiPreview`
- HTTP 访问路径：`/ops-preview`

## 完整上线前置条件

1. 明确选择并开通持久运行方案：CloudRun，或可稳定打包完整 Python 依赖的云函数。
2. 配置独立的持久 MySQL 数据库，不复用签到系统业务表，不使用临时 SQLite。
3. 通过密钥管理或环境变量配置独立密钥、首个管理员和数据库凭据。
4. 执行数据库迁移、备份恢复演练、权限验收与数据导入核对。
5. 将 `DEPLOYMENT_READ_ONLY` 切换为 `false` 前再次取得生产写入批准。

## 回滚

以下命令只删除本平台新增的验证资源，不应作用于签到系统：

```powershell
tcb hosting delete ops-platform --dir --env-id shengheshu-d2g2zyyl99f6c6fc2
tcb service delete --service-path ops-preview --env-id shengheshu-d2g2zyyl99f6c6fc2
tcb fn delete seiwajyukuPlatformApiPreview --env-id shengheshu-d2g2zyyl99f6c6fc2
```

执行回滚前应再次核对目标名称和路径；当前验证环境按用户要求保留，未执行上述命令。
