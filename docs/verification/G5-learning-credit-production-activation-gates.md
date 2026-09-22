# G5 学分生产激活阶段门禁

状态日期：2026-09-22
代码审计基线：`b3136c7f60506abeae90e74b841e9edd22fa6c90`

本文是执行顺序和证据要求，不是任何生产操作授权。每个 Gate 只能消费已经完成的前置 Gate；通过一个 Gate 不自动授权下一个 Gate。

## 总顺序

```text
GATE-0 Tencent candidate connectivity
  -> GATE-1 R3 RULE_RECONCILIATION
  -> GATE-2 0064 rule mapping/freeze
  -> GATE-3 0065/0066 historical staging schema
  -> GATE-4 historical production DRY-RUN
  -> GATE-5 historical approval
  -> GATE-6 settlement batch infrastructure
  -> GATE-7 operational credit center
  -> GATE-8 member credit UI
```

全链路不变量：`learning_credit_entries` 是最终不可变账本；迁移、DRY-RUN、审核和页面展示均不得绕过 ledger 的幂等键、规则快照与冲销追加模型。历史来源 `LEGACY_SUZHOU_2026_V1` 按原值迁移，不使用当前规则重算。

## GATE-0 Tencent candidate connectivity

- 前置条件：CloudRun stable/candidate、VPC、路由和控制器 provenance 有实时只读证据；生产数据库业务状态未变化。
- 允许操作：仅以独立批准包列明的候选验证动作；任何 URL_PARAMS、revision 或发布单写入必须逐项授权。
- 禁止操作：R3、0064/0065/0066、数据库写入、ledger 写入、settlement 开启。
- 成功证据：定向路由真实激活；候选实例 Ready；预期 runtime commit；liveness 通过；数据库健康 20/20；CLS 证明全部探针命中 candidate；流量恢复；发布上下文关闭。
- 失败停止条件：候选资格、路由、实例、身份、数据库健康、恢复或发布单关闭任一无法证明。
- 独立生产授权：是。

## GATE-1 R3 RULE_RECONCILIATION

- 前置条件：GATE-0 PASS；固定当次 `CONTROL_TOOL_COMMIT`、runtime commit、main CI 与 release manifest；重新读取生产规则、双指纹、迁移、mapping、binding、ledger、settlement 和审计基线；可用备份及恢复路径已确认。
- 允许操作：一次受边界控制的课程规则原子 APPLY；仅执行批准的 `7 KEEP / 4 UPDATE / 14 ADD / 3 REMOVE_UNUSED_PLACEHOLDER`。
- 禁止操作：0064/0065/0066、mapping 创建、binding 冻结、ledger 写入、settlement 开启、其他规则或占位项删除。
- 成功证据：25 条逐字段 canonical；规则版本保持 DRAFT；完整 before/after 审计；第二次执行为 ALREADY_APPLIED；mapping=0；两类 binding 冻结仍为0/19；ledger delta=0；settlement=false。
- 失败停止条件：指纹、commit、14条DRAFT、placeholder引用、6条generic规则、19条binding、ledger或开关任一偏离；事务必须回滚。
- 独立生产授权：是；使用 `2026-09-22-G5.4-C3.3-R3批准包模板.md` 单独申请。

## GATE-2 0064 rule mapping/freeze

- 前置条件：GATE-1 PASS；25条课程规则逐字段 canonical 且仍为DRAFT；6条generic规则唯一PUBLISHED；mapping=0；19条ACTIVE binding两类引用均为空；ledger=0；0064/65/66未应用；新备份可恢复。
- 允许操作：仅执行 `0064_fix_credit_rule_mapping_and_binding_freeze.sql`，然后只读验收。
- 禁止操作：规则内容再编辑、0065/0066、历史导入、ledger写入、settlement开启。
- 成功证据：课程规则版本=PUBLISHED；唯一ACTIVE mapping=1；19个现有binding的generic/course版本引用均冻结；课程事实按既有binding补齐；ledger delta=0。
- 失败停止条件：迁移保护触发、canonical不一致、mapping或binding数量偏离、ledger变化；停止后按备份恢复或批准的前向修复处理。
- 独立生产授权：是；0064的成功会形成冻结引用，不能把down脚本当作通用无损回滚。

## GATE-3 0065/0066 historical staging schema

- 前置条件：GATE-2 PASS；0065/0066未应用；历史staging表不存在；ledger仍为0；备份与空表rollback路径已确认。
- 允许操作：先0065并验收，再0066并验收；只创建历史导入、审核、时间精度所需结构和权限。
- 禁止操作：上传历史Excel、写审核决定、POST ledger、开启settlement、开发或调用历史正式入账。
- 成功证据：0065/0066迁移记录存在；batch/row/item、class mapping、decision结构完整；ledger支持EXACT_DATE/MONTH/YEAR；staging表为空；ledger行数和分值仍为0。
- 失败停止条件：任一步结构或约束不符、ledger发生业务写入、迁移记录不确定；不得继续后续迁移。
- 独立生产授权：是；0065/0066可在同一批准包中顺序执行，但0065验收是0066前的硬检查点。

## GATE-4 historical production DRY-RUN

- 前置条件：GATE-3 PASS；固定来源文件、SHA-256、`LEGACY_SUZHOU_2026_V1`、操作人和批准范围；生产快照及异常口径已确认。
- 允许操作：仅在历史staging中登记批次、解析、班级映射、成员匹配、学分/期间审核和DRY-RUN；每种staging写入必须在批准包列明。
- 禁止操作：向`learning_credit_entries` POST、按当前规则重算历史分、自动确认歧义、忽略blocker、开启settlement。
- 成功证据：来源工作簿/sheet/row/column、原分值、YEAR/MONTH/EXACT_DATE、匹配和审核链可追溯；blocker分类完整；`learning_credit_entries_delta=0`。
- 失败停止条件：文件哈希不符、解析总分不守恒、歧义被自动确认、期间精度不明、ledger delta非0。
- 独立生产授权：是；DRY-RUN仍会写生产staging，因此不是普通只读授权。

## GATE-5 historical approval

- 前置条件：GATE-4结果冻结；所有班级、成员、积分异常和期间blocker都有责任人决定；总人数、总分、异常数和排除数完成双人复核。
- 允许操作：记录审批结论和不可变审批快照；形成未来POST批次输入。
- 禁止操作：审批即POST、修改原Excel、用新规则替换原分值、越过未解决blocker。
- 成功证据：APPROVED范围、审核人、时间、理由、源快照和结果快照完整；审批前后汇总可复算。
- 失败停止条件：任何blocker未关闭、审批范围与DRY-RUN快照漂移、总分不守恒。
- 独立生产授权：是；历史正式POST仍需后续单独授权。

## GATE-6 settlement batch infrastructure

- 前置条件：统一批次设计获业务与技术评审；迁移、API、权限、审计、幂等、故障恢复和冲销测试完成；不得依赖某一种活动的专用POST流程。
- 允许操作：开发并隔离验证批次编排层；接入事实、冻结规则、DRY-RUN、审批和受控POST。
- 禁止操作：在本Gate开发期间打开生产settlement；让批次表替代ledger；覆盖原ledger；为班会/每日读书/优秀分享各造一套POST。
- 成功证据：状态机、稳定幂等键、部分失败续跑、规则快照不可漂移、POST后只追加冲销均有测试。
- 失败停止条件：重跑可重复入账、批次与ledger无法对账、规则可在审批后漂移、失败恢复需覆盖原entry。
- 独立生产授权：开发不需要；迁移、上线及首次生产POST分别需要。

## GATE-7 operational credit center

- 前置条件：GATE-6接口稳定，生产账本与批次语义已明确；权限和敏感字段范围完成设计。
- 允许操作：开发学员总分、分类、批次、blocker、重复、冲销、历史导入进度、规则版本和来源追踪页面。
- 禁止操作：页面直接写ledger、绕过审批、把估算/DRY-RUN显示为正式积分。
- 成功证据：运营人员无需直接查库即可完成审批、追踪、对账和异常定位；越权和脱敏测试通过。
- 失败停止条件：页面状态与账本不一致、写操作没有审计、来源无法追溯。
- 独立生产授权：普通代码发布按工作流；迁移或生产数据操作另行授权。

## GATE-8 member credit UI

- 前置条件：正式账本已启用且运营闭环可处理异常；个人summary/entries API口径稳定；隐私和本人范围验证完成。
- 允许操作：展示总分、本年度、分类和最近明细；明细解释时间、来源、分值和规则。
- 禁止操作：展示DRY-RUN、未审批或staging记录；前端自行估算；跨学员读取。
- 成功证据：本人范围、分页、分类/年度汇总、YEAR/MONTH/EXACT_DATE展示和冲销解释测试通过。
- 失败停止条件：无法解释分值来源、与ledger对账不一致、存在越权或误导性占位。
- 独立生产授权：普通代码发布按工作流；不包含账本激活或数据写入授权。
