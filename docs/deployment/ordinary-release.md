# 普通代码发布

适用于现有平台服务的代码修复与优化。授权范围以 [工程工作流](../engineering-workflow.md#授权表) 为准。首次资源开通、数据迁移、权限开关与扩容使用独立的获批操作方案。

1. 核验活动仓库、目标分支、当前变更及远端保护规则。只提交本任务文件，保留其他人的修改。普通交付默认 main；被保护或明确要求评审时走 PR。main 已被其他工作树占用时，可从已核验的 origin/main 创建聚焦提交并普通推送 HEAD:main；远端前进时先协调，不强推。
2. 完成影响范围内验证，推送后确认目标提交的 CI。纯文档、技能和 CI 变更以仓库交付为终点，无需重发应用。失败时根据日志修复，不把上一个提交的通过状态作为依据。
3. 应用发布从干净的目标提交构建，使用现有 `scripts/build_provenance.py` 与适用发布清单核验来源。部署前检查当前服务、流量、版本、回退目标和配置；不得根据历史报告中的版本号选发布目标。
4. 核验构建不会隐含执行生产迁移或打开 IAM／数据写入开关。生产容器保持禁止启动自动迁移。若本次需要迁移，先完成独立迁移授权与隔离验证，再继续依赖它的发布。
5. 生产 API `seiwajyuku-platform-api` 必须使用 `scripts/deploy_cloudrun_api.py` 受控发布。脚本通过 `DescribeCloudRunServerDetail` 发现唯一 100% 流量稳定版本，通过 `DescribeVersionDetail` 读取该版本的完整 `VpcConf`，再用官方 Python SDK 调用 `UpdateCloudRunServer`，在同一请求的 `Items` 中显式提交 `DiffConfigItem(Key="VpcConf")`。服务级 `ServerConfig.VpcConf` 不是网络基线；普通代码和配置发布也不接受调用者传入 VPC/subnet 字段。
6. 候选只能用 `ReleaseType=GRAY` 创建。候选状态为 `normal` 后，先通过 `DescribeVersionDetail(candidate)` 逐字段比较 `VpcId`、`VpcCIDR`、`SubnetId`、`SubnetCIDR`；不一致即 `CANDIDATE_VPC_MISMATCH`，在任何 candidate 路由前停止。`ReleaseGray` 返回成功后还必须轮询 `DescribeReleaseOrder` 与服务详情，确认 `TrafficType=URL_PARAMS`、stable/candidate 版本、`UrlParam` token 和默认优先级均匹配；再确认 candidate Pod 就绪。只有 `TARGETED_ROUTE_ACTIVE` 后才可发送带 token 的 `/api/v1/system/build-info`、`/health/live` 和真实访问数据库的 `/api/v1/health` 20/20，并用 `SearchClsLog` 证明全部 23 条探针日志属于 candidate、stable 命中为 0。普通域名的随机百分比响应、HTTP 200、相同 runtime commit 或 ReleaseGray 成功响应不能作为候选身份证明。
7. 先运行只读 dry-run，并审阅稳定版本、稳定/候选请求 VPC、目标提交、环境变量变更键和发布策略。dry-run 不申请上传地址、不上传包、不创建 revision、不改流量：

   ```powershell
   python -m pip install -r scripts/requirements-release.txt
   python scripts/deploy_cloudrun_api.py --commit <40位提交> --source --dry-run
   ```

   生产执行还必须具有当前范围的批准引用；源码模式从干净且等于 `origin/main` 的提交生成包，构建印章写入临时归档而不修改工作树：

   ```powershell
   python scripts/deploy_cloudrun_api.py --commit <40位提交> --source --execute --release-manifest <main的9项CI产物/release-manifest.json> --approval-ref <批准编号> --build-id <构建编号> --promotion ready
   ```

   `--execute` 必须读取该提交在 `main` 通过 9 项 CI 后生成的 release provenance manifest，且 manifest 的 commit 与 `--commit` 完全一致。环境变量变化通过受控 JSON 文件传入；日志只显示键和 `changed`／`unchanged`，仅允许列出的非敏感布尔门禁显示值。数据库 VPC 的只读证据可用 `--expected-database-vpc-id` 增加前置断言。`ready` 完成定向门禁后恢复稳定版本 100% 默认流量；`gray`／`full` 属于后续明确批准的正常流量动作。
8. 历史 `tcb cloudrun deploy` 可以作为排障参考或受控工具内部可能使用的底层构建/上传能力，但不得绕过 wrapper 单独用于生产 API 发布，也不得作为网络继承、候选健康或发布完成证明。2026-09-19 R2A 已证明裸 CLI 新 revision 可能未继承稳定版本的版本级 `VpcConf`。
9. `SubmitServerConfigChangeDiff` 可携带 `DiffConfigItem`，但其语义是“更新配置并使用最新镜像发布”，没有 `UpdateCloudRunServer` 的显式 `DeployInfo`。为同时覆盖源码包、镜像和配置发布，正式路径只保留 `UpdateCloudRunServer`；不要在两条生产路径之间按任务临时切换。
10. API 发布依次记录 `DISCOVER_STABLE → READ_STABLE_VERSION → BUILD_PLAN → CREATE_CANDIDATE → VERIFY_CANDIDATE_CONFIG → TARGETED_HEALTH → TARGETED_ROUTE_ACTIVE → CANDIDATE_INSTANCE_READY → CANDIDATE_IDENTITY_VERIFIED → READY_FOR_RELEASE → TRAFFIC_RESTORED → GRAY → FULL → VERIFIED`；按批准范围可在 `TRAFFIC_RESTORED` 或 `GRAY` 停止。任何失败进入 `BLOCKED`／`FAILED`，不让失败候选获得普通流量。`253=100% / 257=0%` 只代表流量恢复，不代表发布单已关闭；活动 `DescribeReleaseOrder.IsReleasing=true` 会阻止新 candidate。`OperateServerManage` 的收尾动作必须另行确认语义和授权。
11. 发布后 API 核验 `/api/v1/system/build-info`、`/api/v1/health`、部署记录、启动日志及流量；管理端核验其构建信息、登录和受影响页面。涉及独立签到组件时按该组件实际提供的版本、健康与页面入口核验，不把主平台端点套用过去。浏览器超时不等于验收成功。
12. 发布失败按预先核验的恢复方案处理。代码回退不能替代数据库恢复；不要给启动失败或网络断言失败的候选切普通流量，也不删除共享 CloudBase 资源。记录实际结果与未完成事项。
