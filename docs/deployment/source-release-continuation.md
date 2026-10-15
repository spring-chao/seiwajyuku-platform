# 普通源码候选发布中断后续发

用途：受控源码发布在创建候选之后被中断，候选已正常启动、旧版本仍为 100%，但原进程没有继续探针核验与流量发布。不会新建版本、修改网络、修改业务环境变量、取消发布单或运行数据迁移。

只接受原来的同一候选、running GRAY 管理任务及 FLOW 发布单的精确 ID；稳定版本必须仍为指定版本且 100%。稳定及候选的四项 VPC 字段必须一致且完整，候选正常、固定端口 8000。候选环境必须与稳定环境逐项相同，唯一例外是本候选自己的独立启动探针 ID，禁止复用稳定版本探针。

从云端已完成的 CreateVersion 步骤获取原创建时间（北京时间），而非把进程重启时间当成候选启动时间。只接受 24 小时内的创建窗口。保留构建提交、loopback、真实数据库 20/20、23/23 candidate 且 0 stable 的日志证据，以及灰度/全量后的发布单结束核验。原业务门禁不变。

先核验 runtime 提交的 CI 清单，以及当前控制器干净 main 提交的清单和 GitHub 9/9 实际结果；后者与 runtime 可以不同。没有 `gh` 时，控制器通过 GitHub 官方只读 API 执行同样的 CI 校验，可选从进程环境读取 `GH_TOKEN`，不输出凭据。

```powershell
python scripts/deploy_cloudrun_api.py --source --commit <候选runtime提交> --resume-source-candidate <候选版本> --expected-stable-revision <稳定版本> --expected-task-id <任务ID> --expected-release-order-id <发布单ID> --candidate-verification startup-loopback --release-manifest <runtime清单> --control-tool-commit <当前控制器main提交> --controller-release-manifest <控制器清单> --approval-ref <同范围批准引用> --promotion full --dry-run
```

审阅 dry-run 后，将末尾 `--dry-run` 改为 `--execute`。不得将此方式用于已有环境差异、网络修复、任意历史候选或无证明的镜像。普通发布的授权和发布规则仍见 [普通代码发布](ordinary-release.md)。

候选等待期间若已自动缩容至 0，可为该精确源码续发增加 `--warm-resumed-source-candidate`：所有基线校验通过后，仅在实例列表为空时调用一次官方 StartVersionInstance。已有 Running/Pending 实例时不再增加实例，不修改 CPU、内存、最小/最大实例数或网络配置。启动请求超时不重试。新实例 Running 后，按官方返回的 PodId 限定两类 CLS 查询，仍须本实例完整 23 条日志及 20 次数据库成功，避免混用原实例和重启实例的重复探针。普通发布及通用现有候选验证不能使用此开关。

隔离验证：发布网络、原有网络修复、现有候选和启动探针测试共 169 项通过；新增覆盖任务/发布单漂移、候选 VPC/环境差异、探针复用、过期创建窗口、CI 缺失、试图打开业务开关，以及候选构建/数据库/日志证据缺失拒绝。
