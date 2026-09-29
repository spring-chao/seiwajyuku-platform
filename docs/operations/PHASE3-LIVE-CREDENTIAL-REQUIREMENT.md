# Phase 3 真实只读采集：认证前置条件

状态（2026-09-30）：`LIVE_CONTROL_PLANE_EVIDENCE=NOT_COLLECTED`、`LIVE_RUNTIME_EVIDENCE=NOT_COLLECTED`。本次检查的 Codex 进程未发现 `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY` / 临时 Token 环境变量；本机存在 `tcb` 命令，但其认证主体、CAM 有效策略和只读范围无法从当前证据确认，因此未调用 CLI 或生产接口。没有读取浏览器登录态、配置文件内容或任何凭据值。此文不是创建身份或扩大权限的授权。

## 固定对象与能力

- 地域 `ap-shanghai`，环境 `shengheshu-d2g2zyyl99f6c6fc2`，服务 `seiwajyuku-platform-api`。
- `scripts/r3_cloud_read.py` 只调用五个腾讯云接口：`DescribeCloudRunServerDetail`、`DescribeVersionDetail`、`DescribeReleaseOrder`、`DescribeServerManageTask`、`DescribeCloudRunPodList`。
- CAM 身份只需这五个 Action 对该环境的读取权限；策略资源应按腾讯云 CAM 对这些 Action 的环境级资源语法限定，不能给整个账户的 `tcbr:*`。腾讯云将五项列为资源级查询/列表接口，实际策略须由 IAM 管理员在 CAM 中校验生效范围。[官方 CAM 接口表](https://cloud.tencent.com/document/product/598/76364)。
- Runtime 只读检查固定为生产域名上的三个 GET：`/api/v1/system/build-info`、`/health/live`、`/api/v1/health`。不得发送 Cookie、Authorization 或带业务写语义的请求。

供 IAM 管理员核对的最小策略形状（**提案，不自动创建/绑定**）：

```json
{
  "version": "2.0",
  "statement": [{
    "effect": "allow",
    "action": [
      "tcbr:DescribeCloudRunServerDetail",
      "tcbr:DescribeVersionDetail",
      "tcbr:DescribeReleaseOrder",
      "tcbr:DescribeServerManageTask",
      "tcbr:DescribeCloudRunPodList"
    ],
    "resource": ["qcs::tcbr:ap-shanghai:uin/:env/shengheshu-d2g2zyyl99f6c6fc2"]
  }]
}
```

CAM 的现有附加策略、角色继承与实际资源匹配必须一并核对；这段 allow 本身不能证明主体没有其他写权限，也不能代替授权方批准。

## 必须由授权方提供或确认

1. **已有且获准用于本次只读采集的身份**：主体标识、CAM 策略版本/有效期、上述五项 Action 的实际允许范围及无生产写权限的核验记录。身份本身不写入仓库或报告。
2. **认证注入方式**：由授权方提供或确认既有只读身份，并在隔离进程中安全提供 SDK 所需的 ID、Key 和可选临时 Token；不得读取浏览器会话、控制台缓存、历史 JSON、CLI 私有配置、应用的运行时密钥或其他角色凭据。不得在命令行、日志、CI artifact 和报告中输出值。
3. **可信签封密钥**：`--verifier-key-file` 指向预先存在、独立保管、至少 32 bytes 的文件；与 bundle 不在同一输出目录。Windows 文件和父目录需有预配置受限 ACL；collector 只读验证，不创建/修改密钥或 ACL。
4. **运行时实参**：采集前用授权只读方式重新确认唯一 stable revision、其 runtime commit、对应管理任务 ID、无活动发布单，以及 FLOW=100%。旧报告中的 `258` / `2201828` 仅为历史候选值，不可不经复核直接填入。

## 明确排除

不得授予 `ReleaseGray`、`OperateServerManage`、`UpdateCloudRunServer`、`StartVersionInstance`、`StopVersionInstance`、`DeleteCloudRunVersions`、创建版本、修改网络、数据库读写、CAM 管理或 `tcbr:*`。本次也不因持有 SDK 凭据而获得任何生产写授权。

## 执行及验收门禁

只在身份、ACL、动态实参均有证据时，从固定干净提交运行 `scripts/r3_collect_readonly.py --collect-readonly-evidence`。输出文件必须原先不存在；最多一批采集，任何异常失败关闭，不自动重试。离线验证 bundle 的签封、realm、对象和时效；同时确认控制面和 runtime 只读 PASS。采集器固定报告 `production_db_read=NOT_AUTHORIZED`，因此即使这两项通过，`PHASE_3` 仍不能判 PASS，不能进入生产写适配或 R3。
