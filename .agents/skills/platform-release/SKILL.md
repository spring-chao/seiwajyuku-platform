---
name: platform-release
description: 准备、执行或核验盛和塾运营平台现有服务的普通代码发布。用于修复优化后的交付或发布故障处理；纯代码编辑不触发。
---

按 [普通发布流程](../../../docs/deployment/ordinary-release.md) 核验目标提交、CI、构建来源与在线状态。授权分类见 [工程工作流](../../../docs/engineering-workflow.md#授权表)，不重复展开无关历史上线文档。

- 已有同范围有效授权沿用；缺少授权时先完成独立的构建、检查和恢复准备，再提出具体缺口。
- 普通发布不隐含生产数据迁移、IAM 写入、密钥或资源变更；遇到这些依赖只暂停相应动作。
- 生产 `seiwajyuku-platform-api` 的 CloudRun 新版本必须经 `scripts/deploy_cloudrun_api.py`：从唯一 100% 稳定 revision 的 `DescribeVersionDetail` 继承完整 `VpcConf`，在 `UpdateCloudRunServer.Items` 显式提交，并在任何 candidate 路由前逐字段回读断言。服务级空 `VpcConf` 和裸 `tcb cloudrun deploy` 都不能作为继承或完成证明。
- 候选网络断言通过后，使用一次性 `URL_PARAMS` 定向路由核验目标提交、liveness 和数据库健康 20/20；稳定版本保持默认。失败候选不得获得普通流量。普通应用发布不得接受调用者覆写 VPC/subnet；网络迁移需要独立授权流程。
- 发布必须区分提交成功、启动成功、流量生效与业务验收。报告目标提交、实际检查、发布结果及未完成项。
