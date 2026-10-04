# 学习会合影与提交启用

授权来源：2026-10-04 用户要求开启小组学习会合影并可提交本次学习会，进入上线准备；微信正式提审和发布由用户操作。

## 生产配置

复用当前环境既有私有 CloudBase 存储、`studyEvidenceCleanup` 云函数及已有服务间 Token。将 `STUDY_MEETING_EVIDENCE_ENABLED` 与 `STUDY_EVIDENCE_CLEANUP_ENABLED` 设为 `true`，补齐既有每日触发器；保留7天访问期限与15分钟清理宽限。学习会提交已开启。保持数据库、VPC、IAM、月更及积分结算门禁现状；不执行迁移、学员照片回填或真实学习会测试登记。

容器实际没有存储运行身份，不能把本地 CloudBase 登录临时凭据注入生产，也不新建宽权限环境 API Key。现有事件云函数复用自动注入的临时身份：在同一函数增加 `/study-evidence-storage` 服务路径及明确启用配置，后端配置 `CLOUDBASE_STORAGE_BRIDGE_URL`。HTTP 请求须持有已有服务间 Token，只允许 `study-meetings/production/YYYY/MM/<随机32位ID>.jpg|png` 的单对象 PUT/GET/HEAD/DELETE，签名90秒到期。PUT 绑定图片类型、私有 ACL 和禁止覆盖条件。后端直接传输图片到既有桶，前端不接触存储凭据或签名 URL；原定时事件继续走既有清理入口。拒绝重定向、任意桶和路径，错误中不保留签名 URL。

网关默认剥去触发路径前缀，云函数只接受映射后的根路径或开启透传时的完整固定路径；两种路径均要求服务间 Token，其他子路径拒绝。保持既有网关设置。参见[路径透传说明](https://docs.cloudbase.net/en/service/path-passthrough)。

## 发布验收

发布前通过隔离测试，确认一张真实 JPG/PNG、5MB限制、剥离图片元数据、组织范围隔离、重试复用草稿、重复提交幂等及未完成课程可登记。

新候选必须经受控源码发布与 main 的9项 CI 来源验证。保留23条容器内 HTTP、20次真实数据库健康查询和 CLS 候选身份验证；若开启合影，再使用候选运行身份对单个随机合成图片执行私有 Put/Get/Delete。验证匿名访问不可用、内容读回一致、删除后对象不存在。失败时不切正常流量；失败补偿仅删除本次合成对象，不访问真实学员合影或创建业务记录。

## 月更在云托管空闲后的可靠执行

最终观察发现旧小时后台任务两次报 MySQL 2013 断连；启动检查通过不能证明空闲后的任务可用。[云托管开发说明](https://docs.cloudbase.net/run/develop/developing-guide)明确要求请求结束前完成工作，空闲后后台线程没有 CPU 保证。生产启动补偿改为在启动结束前完成，不保留小时后台线程。复用既有 `studyEvidenceCleanupDaily` 每日事件，在同一云函数中并行等待合影清理和固定 `/api/v1/internal/learning-cycle-monthly` 请求，两者分别返回脱敏汇总，失败不能阻止另一项运行。

月更入口仅接受空对象和已有服务间 Token，不能指定班级、日期、快照或学分动作。沿用月更、只读及生产写入开关；按上海时区登记月份计算，同月重复执行不加次数。定时请求不应用历史修复快照，只做正常月度递进与只读核验；原受审阅摘要保护的启动修复保留。没有新增函数、密钥、触发器或扩容。已有志工读入口保留单班补偿。

现有 `/api/v1/system/environment` 返回提交、合影与清理布尔值，以确认生产进程实际读取新配置。日志仅保留安全检查结果，不包含凭证或合影 URL。每日清理仅处理过期合影元数据及受控存储前缀，不删除学习会、参与人员、学习内容或积分记录。

## 回退与交付

保留发布前唯一100%稳定版本作代码/配置回退目标。准备浏览器演示、微信预览二维码与发布前状态报告；真机相册、拍照、权限提示和真实登记验收由用户测试，未执行项保持待验收。正式微信提审和发布不自动执行。

运行身份依据：[CloudBase 云函数环境变量](https://docs.cloudbase.net/cloud-function/function-configuration/env)。HTTP 接入依据：[CloudBase HTTP 访问云函数](https://docs.cloudbase.net/service/access-cloud-function)。签名依据：[COS 请求签名](https://cloud.tencent.com/document/product/436/7778)。定时触发器配置依据：[CloudBase 触发器文档](https://docs.cloudbase.net/cli-v1/functions/trigger)。
