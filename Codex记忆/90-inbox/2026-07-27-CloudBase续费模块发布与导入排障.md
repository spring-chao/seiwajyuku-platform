---
memory_id: seiwajyuku-renewal-cloudbase-deploy-import-lessons
type: capture-candidate
status: inbox
updated: 2026-07-27T14:40:03+08:00
source: 2026-07-27 CloudBase 版本022至023部署记录、容器启动日志和线上预检验证
confidence: high
sensitivity: internal
project: seiwajyuku-platform
tags: [codex-memory, cloudbase, mysql, deployment, troubleshooting]
---

# CloudBase续费模块发布与导入排障

CloudBase 发布必须分别验证构建、容器启动、流量、API 路由和静态前端；命令显示“提交完成”不代表用户已经能看到或使用新功能。

## 可复用经验

- 使用 `DescribeCloudRunDeployRecord` 判断版本状态；对 `deploy_failed` 再用 `DescribeCloudRunProcessLog` 获取容器启动异常。
- MySQL 新表凡引用既有字符串外键，必须显式使用 `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`，否则可能因字符集或排序规则不兼容导致容器迁移启动失败。
- Excel 原始值可能包含 `datetime`；保存整批预览 JSON 和单行原始 JSON 时都必须使用可控的日期序列化，例如 `json.dumps(..., default=str)`。
- 新 API 未登录返回 `401` 可证明路由存在并启用鉴权；`404` 表示仍在运行旧路由或发布未切换。
- 前端功能需要单独构建并上传 `/ops-platform/`；只发布 CloudRun 后端不会产生可见菜单变化。
- 线上验收应使用真实登录页面和用户已选择的工作簿验证成功结果，而非只依赖本地单元测试。

## 本次证据

- MySQL 外键兼容修复后，CloudRun 版本 022 正常启动。
- Excel 日期序列化修复后，版本 023 在线预检成功返回 795/785/10/0/97。
- 后端测试增加至 25 项并通过；相关提交包括 `8e3ce95`、`7121bd9`、`4198ae8`。

## 边界

- 日志中若含密钥、连接串或个人敏感数据，不得复制到项目记忆。
- 发布状态、地址和版本属于可变化事实，后续使用前仍需实时核验。
