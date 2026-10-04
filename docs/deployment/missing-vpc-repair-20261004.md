# 缺失 VPC 基线恢复与月度更新发布

用户在 2026-10-03 明确要求“解决后端发布被缺失的VPC配置，我只要目标生效”，并在 2026-10-04 要求继续完成。此次授权覆盖本服务的缺失网络配置恢复、已验证后端代码发布和此前要求的学习周期月度更新启用。小程序正式发布由用户操作。

## 已核验范围

- 固定服务 `ap-shanghai/shengheshu-d2g2zyyl99f6c6fc2/seiwajyuku-platform-api`。
- 2026-10-04 只读检查：稳定版本 260 为唯一 100% 流量，发布单已结束；其四项 VpcConf 均为空。
- 历史健康版本 258 的完整 VpcConf 与当前数据库所属 VPC/子网一致；数据库控制面地址匹配稳定版本 DATABASE_URL，数据库 running。
- `DescribeVpcs`、`DescribeSubnets` 进一步确认 VPC、子网、CIDR 与上海区域一致。
- 修复配置仍须执行时重新核验，不能将本报告当作实时基线。

## 专用修复入口

`scripts/deploy_cloudrun_api.py --repair-missing-vpc-from-version <历史版本> --expected-stable-revision <稳定版本>` 是单独授权的缺失基线修复模式，不改变普通发布继承稳定版本 VPC 的约束，也不接受调用者传入 VPC/subnet 值。

修复模式只允许：完整缺失的四项配置、同服务历史健康版本、干净 main 源码、已通过 9 项 CI 的控制器/构建来源、明确批准引用，以及可选的 `LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED=true`。默认继承数据库连接；显式使用下述端点恢复选项时，只允许同数据库、相同身份凭据的已验证内网端点。不变更 IAM/学分门禁、启动迁移、容量或共享网络资源。

控制器从三个控制面来源交叉验证网络，并在上传后、创建候选前再次验证稳定版本、环境指纹及网络。候选只通过 GRAY 创建，回读完整 VPC 后才验证候选健康。20/20 数据库健康及 23/23 候选日志身份通过后才切普通流量。灰度和全量切换都等待实际流量；全量必须同时确认发布单结束。异常恢复原稳定版本流量，不给未验证候选普通流量。

只读准备命令：

```powershell
python scripts/deploy_cloudrun_api.py --commit <main提交> --source --dry-run --repair-missing-vpc-from-version seiwajyuku-platform-api-258 --expected-stable-revision seiwajyuku-platform-api-260 --approval-ref USER-20261003-VPC-MONTHLY --env-file <仅月更开关的JSON>
```

执行增加 `--release-manifest <该main提交CI产物> --build-id <CI编号>`，使用 `--execute --promotion full`。生产完成证据单独保存；本实现文档不表示已经上线。

## 继续已创建的修复候选

候选版本变为 `normal` 与发布单进入可路由状态并非同一个事件。发布器等待对应发布单及正常服务状态后，才提交定向路由。全量切换在序列化请求中显式加入 `CloseGrayRelease=true`；参数与已安装的官方 CloudBase CLI 3.6.1 `CloudrunService.promote` 一致，旧版 Python 类型模型未包含该字段。

若候选已创建、稳定版本仍为唯一 100% 且发布单仍在 FLOW 阶段，可在同一缺失 VPC 修复授权内增加 `--resume-repair-candidate <候选> --expected-task-id <任务> --expected-release-order-id <发布单>`。入口只接受精确匹配的运行中 GRAY 任务和当前发布单，并重新验证稳定环境、网络及候选的完整配置；不上传源包、不创建新版本。其他进行中的发布、已经分流或身份变化一律停止。

候选的 `--commit` / `--release-manifest` 仍指向原已部署构建；修复后的控制器通过 `--control-tool-commit <最新main提交> --controller-release-manifest <该提交CI产物>` 独立验证干净 main 与 9/9 CI。继续发布仍必须完成定向路由、Pod、20/20 数据库健康和 23/23 候选日志核验，不能跳过验证直接切换普通流量。

参考：[腾讯云访问 MySQL 的网络要求](https://cloud.tencent.com/document/product/1243/49231)、[ReleaseGray API](https://cloud.tencent.com/document/product/1243/75872)。

## 同实例内网数据库端点恢复

候选 262 的完整 VPC 与构建提交均正确，真实 HTTP 数据库健康却失败（0/20），因此未获得普通流量。只读控制面核验发现：稳定版本 260 使用该数据库的公网端点；同服务健康版本 258 使用控制面当前 `PrivateNetAddress`，用户名、密码、数据库名称、驱动与查询参数均一致，只有地址和端口不同。

用户明确要求自行解决后端 VPC 发布并使目标生效；同实例的内网端点恢复属于本次网络故障修复，不涉及凭据、权限或数据库内容变更。新增显式选项 `--restore-verified-private-database-endpoint`，只允许新建的缺失 VPC 修复候选。调用者仍不能在环境文件中提供 DATABASE_URL 或任意端点。

发布器将历史健康版本、数据库 running 状态、相同 VPC/子网与当前内网端点交叉匹配，要求稳定版本使用同实例的已知公网/内网端点，并逐项保证用户名、密码、数据库、驱动、查询参数不变。只替换原 URL 的主机和端口，保留凭据原始字节。上传后再次核验元数据，端点或凭据变化即停止；候选回读环境完整一致后，仍必须完成真实健康与日志身份门禁。摘要只显示 DATABASE_URL 键的 changed/unchanged，不输出地址或凭据。普通发布路径不受此选项影响。

## 本服务的候选容器自检

2026-10-04 的修复候选 261 已拥有完整且正确的 VPC，但本服务接受 `ReleaseGray(URL_PARAMS)` 后，发布单仍保持 FLOW、没有 token，候选普通流量为 0。官方文档示例中的 `GrayType=FLOW` 实际返回 `InvalidParameter`，已停止该尝试并确认稳定版本 260 为 100%。不能据请求成功就判断定向路由生效。

为在本次明确授权的缺失 VPC 修复中继续验证，新增 `--candidate-verification startup-loopback`。此模式只接受新建的源码修复候选，普通发布和继续既有候选不能使用。发布器生成一次性随机标记，继承原端口 8000 并完整回读候选环境和网络；不会接受用户提供的探针地址、认证头或数据库连接。

候选在应用启动后对固定 `http://127.0.0.1:8000` 发出 23 次 HTTP 请求：构建信息、liveness、真实数据库健康 20 次、再次核验构建信息。健康接口仍执行真实的 `SELECT 1`。结构化结果仅记录标记哈希、完整提交、计数、固定地址及异常类型，不记录响应、凭据或业务数据。发布器要求候选 Pod Running、结果中的提交与批准构建一致、20/20 数据库健康通过，同时通过 CLS 证明全部 23 条带标记的请求来自候选、稳定版本 0 条。在这些证据到齐前，稳定版本必须保持 100% 普通流量。证据缺失、失败、数量错误、身份混入或配置变化均阻止分流。

通过后才执行原有 5% 灰度及 100% 全量切换，并核验线上提交、数据库健康及发布单已结束。`/api/v1/system/environment` 增加不含敏感信息的月更开关状态，用于区分配置写入和公开服务实际启用；无需改变 IAM、学分或业务写入门禁。

月度启动/每小时任务输出 `MONTHLY_REFRESH_RESULT`，只包含构建提交、时间、扫描/更新/失败的汇总数量和开关状态。发布核验可确认实际任务运行，不能将成功开关或 0 条错误日志替代真实的执行汇总；日志不含人员资料或班级业务记录。

本次用户明确要求自行解决发布故障并继续完成，卡住的零流量候选 261 需先结束其发布任务。控制台/API Inspector 浏览器访问超时，使用已安装官方 CloudBase CLI 3.6.1 的 `CloudRunService.rollback` 实际实现核验请求语义：`OperateServerManage` 携带固定 `TaskId` 和 `OperateType=go_back`。只对本次任务 2283377、发布单 2775793 执行一次，并在请求前再次确认 candidate=261、current=260、stable 100%、candidate 0%、GRAY running。2026-10-04 07:55（北京时间）回读 task=`stopped`、发布单已结束、260 仍 100%，请求号 `1717cdf4-69d1-4c64-b1b6-2020910c4748`。此记录不表示取得了 API Inspector 截图，也不授权未来未知发布单的收尾动作；不修改通用发布器的 cancel/go_back/done 行为。
