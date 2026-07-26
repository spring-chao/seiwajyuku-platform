---
memory_id: seiwajyuku-cloudbase-readonly-preview-20260726
type: capture-candidate
status: superseded
updated: 2026-07-27T00:20:00+08:00
source: user-authorized deployment and verified online smoke test 2026-07-26
confidence: medium
sensitivity: internal
project: seiwajyuku-platform
tags: [codex-memory, cloudbase, deployment, isolation]
superseded_by: "[[2026-07-27-当前交付与部署基线]]"
---

# CloudBase 只读隔离部署

在与签到系统共用 CloudBase 环境时，使用独立静态目录、独立函数和独立访问前缀部署本平台已验证可行。

## 已部署资源

- 环境 ID：`shengheshu-d2g2zyyl99f6c6fc2`
- 静态目录：`ops-platform/`
- 平台函数：`seiwajyukuPlatformApiPreview`
- HTTP 前缀：`/ops-preview`
- 在线验证：门户和健康探针返回 HTTP 200，写入请求返回 HTTP 403。

## 隔离边界

- 未修改签到函数 `checkinApi`。
- 未修改签到 HTTP 路径 `/api`、`/api/*`。
- 未覆盖静态托管根目录的签到页面。
- 未开通 CloudRun 或新的付费资源。

## 当前限制

当前部署是只读可行性探针，不是完整生产系统。Python 运行依赖和持久数据库未配置，登录及业务写入被禁用。

详细证据与回滚命令见 `docs/verification/腾讯云CloudBase部署可行性验证.md`。
