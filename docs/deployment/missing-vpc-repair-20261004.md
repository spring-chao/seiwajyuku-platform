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

修复模式只允许：完整缺失的四项配置、同服务历史健康版本、干净 main 源码、已通过 9 项 CI 的控制器/构建来源、明确批准引用，以及可选的 `LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED=true`。不变更数据库连接、IAM/学分门禁、启动迁移、容量或共享网络资源。

控制器从三个控制面来源交叉验证网络，并在上传后、创建候选前再次验证稳定版本、环境指纹及网络。候选只通过 GRAY 创建，回读完整 VPC 后才允许 URL_PARAMS 定向核验。20/20 数据库健康及 23/23 候选日志身份通过后才切普通流量。灰度和全量切换都等待实际流量；全量必须同时确认发布单结束。异常恢复原稳定版本流量，不给未验证候选普通流量。

只读准备命令：

```powershell
python scripts/deploy_cloudrun_api.py --commit <main提交> --source --dry-run --repair-missing-vpc-from-version seiwajyuku-platform-api-258 --expected-stable-revision seiwajyuku-platform-api-260 --approval-ref USER-20261003-VPC-MONTHLY --env-file <仅月更开关的JSON>
```

执行增加 `--release-manifest <该main提交CI产物> --build-id <CI编号>`，使用 `--execute --promotion full`。生产完成证据单独保存；本实现文档不表示已经上线。

参考：[腾讯云访问 MySQL 的网络要求](https://cloud.tencent.com/document/product/1243/49231)、[ReleaseGray API](https://cloud.tencent.com/document/product/1243/75872)。
