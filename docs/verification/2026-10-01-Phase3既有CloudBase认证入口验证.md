# Phase 3 既有 CloudBase 认证入口验证

## 目标与改动

2026-09-30 的生产诊断确认现有 CLI 登录可用；此前 collector 只接受环境变量认证，因此存在“已有可用认证却不能接入正式采集”的技术缺口。本次补齐认证通道，不生成凭据、不赋予发布能力，不宣称正式 live evidence 已完成。

- `scripts/r3_collect_readonly.py` 新增 `--cloud-auth sdk|cloudbase-cli`，SDK 保持默认。
- CLI 模式调用已安装的官方 npm 包 `@cloudbase/cli@3.6.1`，由 CLI 管理认证。未自行读取私有 auth 文件或导出 key。
- `_ReadPorts` 仍只有五个固定 Describe 闭包；固定地域、环境、服务、版本格式、任务号、分页，不开放任意 Action/自由命令。没有引入 controller/journal/write adapter。
- Node 直接执行官方 CLI entry，无 shell、无交互 stdin，响应与错误原文只留在子进程捕获内存。包装层不重试，单调用 10 秒，输出限制 16 MiB；只接受 CLI 3.6.1 `data` JSON 文档。继承环境中强制 TLS 证书验证并移除 `NODE_OPTIONS` preload；安装目录作为 cwd，避免当前仓库 `.env`/项目认证配置被隐式加载。
- 原有签封、字段校验、redaction、live/fixture realm、可信 key/ACL、前后 FLOW 包夹、60 秒 TTL/30 秒窗口和 DB `NOT_AUTHORIZED` 不变。实际 CLI 内部认证行为与主体有效权限仍属于部署环境前置，离线测试不证明它们。

其他 CLI 版本、非核验 npm shim 布局、缺失安装包、解析错误、输出来历不符、超时及缺失 key 均失败关闭；不自动安装工具或重新登录。

## 离线验证

命令（独立测试库，不连接腾讯云）：

```text
python -m pytest tests/test_r3_cloudbase_auth.py tests/test_r3_readonly_evidence.py tests/test_r3_execution_envelope.py tests/test_r3_envelope_journal.py -q
```

结果：`165 passed`。Windows pytest 退出时报告已有临时 symlink 清理 `PermissionError`，未影响测试结果与退出码；不是生产异常。

新增测试覆盖：五个固定读取、固定对象参数、无 shell/交互 stdin、不安全 TLS 与 preload 隔离、非法版本/任务不发起调用、超时/错误/非 JSON/尾随信息/过大输出一次失败关闭、原始 secret 不输出、realm 隔离、官方包名与版本核验、CLI 模式仍必须先通过 trusted key 检查、没有 SDK 环境变量时仍可选择 CLI 通道、沿用 service 校验/redaction、无写方法。

## 生产与交付状态

本次修改未运行正式 collector，未查询生产 DB，也未执行腾讯云控制面写入、R3、迁移、staging、ledger 或权限变更。

```text
CLI_AUTH_CHANNEL = IMPLEMENTED_AND_OFFLINE_TESTED
FORMAL_LIVE_EVIDENCE = NOT_COLLECTED
PRODUCTION_DB_READ = NOT_AUTHORIZED
PRODUCTION_READY = false
R3 = NOT_AUTHORIZED
PHASE_4 = NOT_AUTHORIZED
production writes = 0
APPLY_CALL_COUNT = 0
```

当前 260 与关闭发布单 2747514 是先前诊断时的事实，不替代真正运行 collector 前的再次核验。具体未证明项见 [生产诊断报告](2026-09-30-腾讯云既有认证与260生产只读诊断.md) 与 [认证前置条件](../operations/PHASE3-LIVE-CREDENTIAL-REQUIREMENT.md)。

PR CI 需以本次最终提交再次运行；之前 head 的绿色结果不能替代本次改动验证。main push provenance、manifest 和生产批准包不能引用未合并的 head 为生产控制器。
