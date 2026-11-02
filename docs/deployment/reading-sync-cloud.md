# 读书云端同步运行约定

2026-11-02 用户批准服务器独立运行、有效来源自动保存和自动确认入账，仅适用于22个正式班级的 DAILY_READING。其他类别的确认门禁不变。

## 已部署组件

- 既有 API：`seiwajyuku-platform-api`，签名接口 `/api/v1/internal/reading-sync`，复用来源、结算、持久入账任务和账本去重；继续使用 `scripts/deploy_cloudrun_api.py` 发布，完整继承稳定版本 VPC 和其他环境变量。
- 独立采集器：`seiwajyuku-reading-sync`，1核/2GiB、最小0最大1实例，原始 Excel 完整核验后才交给 API。浏览器显式使用 `zh-CN` 和 `Asia/Shanghai`。源码在 `apps/reading-sync-worker`。
- 调度器：`readingSyncDispatch`，Nodejs18.15、128MiB、180秒。一个事件只处理一个班级区间，再签名派发下一事件。无空闲每分钟轮询。源码在 `cloudfunctions/reading-sync-dispatch`。
- 固定映射：`apps/platform-api/app/services/reading_sync_classes.json`。22班组织与来源 ID 已通过两端正式列表核验；不是人员档案。该文件随 API 和采集器发布，避免超过云托管环境变量5KB限制。

查询必须使用学习平台的 `source_title`，不能使用运营端班级别名。例如运营端“苏州不一班”对应学习平台“姑苏相城分中心不一班”。2026-11-02 对现有采集镜像曾通过精简的运行配置 `READING_SYNC_CLASS_MAP` 修复查询词；该配置仍为同一22班及同一来源ID，仅含三个公开映射字段，未改变学分规则。后续发布必须保留或核验该覆盖，不能把凭据和映射一起直接清空。

## 凭据和调度

学习平台账号密码只配置到采集器运行环境；不提取用户浏览器 Cookie 或隐藏令牌。API、采集器和调度事件使用独立签名密钥。签名校验失败不进入采集或入账。密钥、密码及含密钥的部署配置不得提交仓库或输出日志。

定时器分别为 `readingSyncWeekly`（`0 0 9 ? * MON *`）和 `readingSyncMonthly`（`0 0 9 1 * ? *`）。北京时间17:30的一次性无业务探针已实测在09:30 UTC执行，验证了本环境Cron使用北京时间。探针已删除。

定时器必须携带 `CustomArgument`，其 JSON 为 `timer_signature` 字段，值是调度密钥对 `timer\n<TriggerName>` 的 HMAC-SHA256 十六进制摘要。该证明属于运行配置，不进 Git。代码会丢弃定时事件指定的日期、序号等，只按服务器北京时间计算当前任务。人工验收及内部续跑事件另外校验时间和完整事件签名。

`cloudbaserc.json` 不管理该函数的定时器，避免普通代码更新清除或重建缺少签名证明的触发器。更新函数代码前后核验现有环境变量、角色及两个触发器均未变化。仅部署此函数，不使用 `--all` 修改合影维护等其他函数。

## 日期、收据和异常

周一处理上周一至周日，跨月拆成两个区间。每月1日仅处理上月末日所在周的周一至月末；1日同时为周一时周任务跳过。只跳过同班级、同精确区间已经完整 SUCCESS 的收据，不复核上月整月。

来源密码失效、验证码、下载失败或映射不唯一时有界重试并记录失败，不伪造成功。未知身份不强配；有效行继续入账，但班级保持 PARTIAL_PENDING_IDENTITY。已暂停、规则变化、指纹变化等 NEEDS_REVIEW 不自动绕过。来源及账本按业务键去重，文件 SHA256 仅用于追溯。

持久收据使用现有 `audit_logs` 的 `learning.reading_sync.machine`，并刷新实际来源和入账状态。没有新增数据库表或迁移。只记录班级、区间、文件哈希、数量和固定错误代码，不记录原始人员明细。

新函数的真实执行应查询 CloudBase CLS；本次发现旧 `GetFunctionLogs` 对新函数返回空，即使任务已执行。可用 `tcb logs search` 按 `function_name`、`request_id` 查询；业务完成以 API 收据及实际入账状态为准，不能以请求受理或日志接口返回200替代。

## 停止和恢复

需要停止自动采集时，仅关闭以上两个定时器；已保存来源和已入账结果保留。必要时通过受控 API 配置发布关闭 `READING_SYNC_ENABLED`，不改签到或其他学分开关。恢复时核验凭据、版本和收据，再只重试未完成区间；不重建已经入账的事实，不强制恢复 NEEDS_REVIEW。

云端独立续跑验收后，旧 Codex 本机周任务和月任务已移除。电脑或 Codex 关闭不影响服务器定时与续跑。
