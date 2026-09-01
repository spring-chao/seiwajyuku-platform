# V1.3-C0 小组学习会学习内容映射人工审核清单

> 状态：PENDING_BUSINESS_REVIEW。本文档只提供证据和待确认项，不写入正式 mapping、不赋予正式积分。

## 审核口径

- 基线 commit：`51ce48fe2cd70314acb00be01af0a67455cac6b0`
- 主清单采用最新真实班级投影口径：4 个冲突 + 11 个缺失 = 15 个审核项。
- 主清单只覆盖当前纳入小组学习会 36 周期计划的真实班级；模板层但暂无真实班级影响的异常放入补充附录。
- 1/26 等写法统一解释为 `cohort_month=1, learning_cycle_index=26`，不使用自然月份替代学习周期。
- 实际周期状态来自 `class_learning_cycles`；当前审计的实际运行状态为 `NOT_PROVIDED`，没有虚构 OPEN 周期。

## 汇总

| 项目 | 数量 |
|---|---:|
| 144 模板周期 | 144 |
| MATCHED | 129 |
| 主清单 MAPPING_CONFLICT | 4 |
| 主清单 MAPPING_MISSING | 11 |
| 主清单审核项 | 15 |
| 模板补充项 | 8 |
| 可安全自动修复 | 0 |
| 全部 REVIEW_REQUIRED | 23 |
| P0 / P1 / P2 | 0 / 15 / 8 |
| 无二维码必学视频投影 | 22 |
| 历史二维码节点 | 15 |

### 模板层计数说明

仓库全模板 mapping 文件的原始计数是 5 个冲突 + 10 个缺失；其中包含当前真实班级范围之外的 1 月、10 月模板异常。C0 主清单按真实班级投影重新计数为 4 个冲突 + 11 个缺失，模板异常不被删除，见补充附录。

## 主清单汇总表

| ID | 模板 | 周期 | 状态 | 原内容 | 候选规则 | 影响班级 | 优先级 | Codex建议 | 是否人工确认 |
|---|---|---:|---|---|---|---|---|---|---|
| c0-mapping_conflict-4-26-9c067c09 | 4月开班模板 | 26 | MAPPING_CONFLICT | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、1.哲学手册编制检视+研讨<br>2.三大委落地经验和案例分享编写辅导（如线上辅导，小组学习会可改为半天） |  | 吴越二班 | P1 | 人工对照候选原始文件，确认唯一流程；若只是重复文件，确认后再做 alias/去重标准化。 | 是|
| c0-mapping_missing-4-28-efd01ea0 | 4月开班模板 | 28 | MAPPING_MISSING | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、人财培养体系研讨；（如线上研讨，小组学习会可改为半天） |  | 吴越二班 | P1 | 人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。 | 是|
| c0-mapping_missing-4-29-3905601f | 4月开班模板 | 29 | MAPPING_MISSING | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、人财培养体系研讨；（如线上研讨，小组学习会可改为半天） |  | 吴越二班 | P1 | 人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。 | 是|
| c0-mapping_conflict-7-26-647bcd47 | 7月开班模板 | 26 | MAPPING_CONFLICT | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、1.哲学手册编制检视+研讨<br>2.三大委落地经验和案例分享编写辅导（如线上辅导，小组学习会可改为半天） |  | 吴越一班 | P1 | 人工对照候选原始文件，确认唯一流程；若只是重复文件，确认后再做 alias/去重标准化。 | 是|
| c0-mapping_missing-7-32-c3b597a0 | 7月开班模板 | 32 | MAPPING_MISSING | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、哲学手册编制检视+研讨 |  | 吴越一班 | P1 | 人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。 | 是|
| c0-mapping_missing-7-33-b919d5f4 | 7月开班模板 | 33 | MAPPING_MISSING | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、人财培养体系研讨；（如线上研讨，小组学习会可改为半天） |  | 吴越一班 | P1 | 人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。 | 是|
| c0-mapping_missing-7-34-d52145ac | 7月开班模板 | 34 | MAPPING_MISSING | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、人财培养体系研讨；（如线上研讨，小组学习会可改为半天） |  | 吴越一班 | P1 | 人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。 | 是|
| c0-mapping_conflict-7-26-b3af5cdb | 7月开班模板 | 26 | MAPPING_CONFLICT | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、1.哲学手册编制检视+研讨<br>2.三大委落地经验和案例分享编写辅导（如线上辅导，小组学习会可改为半天） |  | 圆融五班 | P1 | 人工对照候选原始文件，确认唯一流程；若只是重复文件，确认后再做 alias/去重标准化。 | 是|
| c0-mapping_missing-7-32-d1731f57 | 7月开班模板 | 32 | MAPPING_MISSING | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、哲学手册编制检视+研讨 |  | 圆融五班 | P1 | 人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。 | 是|
| c0-mapping_missing-7-33-7bf18e98 | 7月开班模板 | 33 | MAPPING_MISSING | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、人财培养体系研讨；（如线上研讨，小组学习会可改为半天） |  | 圆融五班 | P1 | 人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。 | 是|
| c0-mapping_missing-7-34-3d4eef64 | 7月开班模板 | 34 | MAPPING_MISSING | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、人财培养体系研讨；（如线上研讨，小组学习会可改为半天） |  | 圆融五班 | P1 | 人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。 | 是|
| c0-mapping_conflict-7-26-4d819cf3 | 7月开班模板 | 26 | MAPPING_CONFLICT | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、1.哲学手册编制检视+研讨<br>2.三大委落地经验和案例分享编写辅导（如线上辅导，小组学习会可改为半天） |  | 吴越三班 | P1 | 人工对照候选原始文件，确认唯一流程；若只是重复文件，确认后再做 alias/去重标准化。 | 是|
| c0-mapping_missing-7-32-98e23dee | 7月开班模板 | 32 | MAPPING_MISSING | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、哲学手册编制检视+研讨 |  | 吴越三班 | P1 | 人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。 | 是|
| c0-mapping_missing-7-33-aece74c9 | 7月开班模板 | 33 | MAPPING_MISSING | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、人财培养体系研讨；（如线上研讨，小组学习会可改为半天） |  | 吴越三班 | P1 | 人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。 | 是|
| c0-mapping_missing-7-34-a745ffa0 | 7月开班模板 | 34 | MAPPING_MISSING | 全天或半天、经营分析会实操观摩、上月班级学习会的作业检视、人财培养体系研讨；（如线上研讨，小组学习会可改为半天） |  | 吴越三班 | P1 | 人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。 | 是|

## 15 项主清单逐项证据与业务确认

### c0-mapping_conflict-4-26-9c067c09 · MAPPING_CONFLICT · 4月模板 / 第26周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 4月开班模板 |
| learning_cycle_index | 26 |
| flow_key | — |
| 候选流程 | Y3-C26-COHORT-1-4-7-c5846a2908、Y3-C26-COHORT-1-4-7-3131994926 |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

cohort_month=4, learning_cycle_index=26, year_index=3 命中 2 个候选流程，无法安全选择唯一 flow。

**Codex 建议**

人工对照候选原始文件，确认唯一流程；若只是重复文件，确认后再做 alias/去重标准化。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 吴越二班 | — | 2026-04 | 26 | 2028-05 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：【2026版】第3学年班会+小组会流程/【2026版】第3学年班会+小组会流程/【第26个月】（2026版1、4、7月开班）《哲学手册编制2》视频学习+研讨+经验分析会检视发表+三大委落地经验和案例分享+人生&经营哲学发表+小组学习会-经营分析会观摩+哲学手册编制研讨+三大委案例分享辅导0828.docx
- 原始文件：【2026版】第3学年班会+小组会流程/【2026版】第3学年班会+小组会流程/【第26个月】（2026版1、4、7月开班）《哲学手册编制2》视频学习+研讨+经验分析会检视发表+三大委落地经验和案例分享+人生&经营哲学发表+小组学习会-经营分析会观摩+哲学手册编制研讨0828.docx
- 原始文件：第三年学习计划（2026版20250804）.xlsx
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第1步：经营分析会实操观摩
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第2步：企业参访、企业经营者分享；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第3步：近期读书打卡分享情况；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第4步：上月班级学习会课后作业的检视、分享、辅导；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第5步：哲学手册编写行动计划的检视+研讨；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第6步：三大委建立及运行经验总结分享稿编写的辅导；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第7步：近期重点工作沟通交流；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第8步：空巴。
- `Y3-C26-COHORT-1-4-7-3131994926` 第1步：经营分析会实操观摩
- `Y3-C26-COHORT-1-4-7-3131994926` 第2步：企业参访、企业经营者分享；
- `Y3-C26-COHORT-1-4-7-3131994926` 第3步：近期读书打卡分享情况；
- `Y3-C26-COHORT-1-4-7-3131994926` 第4步：上月班级学习会课后作业的检视、分享、辅导；
- `Y3-C26-COHORT-1-4-7-3131994926` 第5步：哲学手册编写行动计划的检视+研讨；
- `Y3-C26-COHORT-1-4-7-3131994926` 第6步：三大委建立及运行经验总结分享稿编写的辅导；
- `Y3-C26-COHORT-1-4-7-3131994926` 第7步：近期重点工作沟通交流；
- `Y3-C26-COHORT-1-4-7-3131994926` 第8步：空巴。

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

### c0-mapping_missing-4-28-efd01ea0 · MAPPING_MISSING · 4月模板 / 第28周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 4月开班模板 |
| learning_cycle_index | 28 |
| flow_key | — |
| 候选流程 |  |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

按 cohort_month + learning_cycle_index + year_index 未找到对应的小组学习会流程；不能用自然月份或相邻周期猜测。

**Codex 建议**

人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 吴越二班 | — | 2026-04 | 28 | 2028-07 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：第三年学习计划（2026版20250804）.xlsx
- 原始流程步骤：未找到对应流程；以下计划表任务仅作为待核对线索。
- [计划表] 小组学习会：全天或半天
- [计划表] 小组学习会：经营分析会实操观摩
- [计划表] 小组学习会：上月班级学习会的作业检视
- [计划表] 小组学习会：人财培养体系研讨；（如线上研讨，小组学习会可改为半天）

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

### c0-mapping_missing-4-29-3905601f · MAPPING_MISSING · 4月模板 / 第29周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 4月开班模板 |
| learning_cycle_index | 29 |
| flow_key | — |
| 候选流程 |  |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

按 cohort_month + learning_cycle_index + year_index 未找到对应的小组学习会流程；不能用自然月份或相邻周期猜测。

**Codex 建议**

人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 吴越二班 | — | 2026-04 | 29 | 2028-08 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：第三年学习计划（2026版20250804）.xlsx
- 原始流程步骤：未找到对应流程；以下计划表任务仅作为待核对线索。
- [计划表] 小组学习会：全天或半天
- [计划表] 小组学习会：经营分析会实操观摩
- [计划表] 小组学习会：上月班级学习会的作业检视
- [计划表] 小组学习会：人财培养体系研讨；（如线上研讨，小组学习会可改为半天）

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

### c0-mapping_conflict-7-26-647bcd47 · MAPPING_CONFLICT · 7月模板 / 第26周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 7月开班模板 |
| learning_cycle_index | 26 |
| flow_key | — |
| 候选流程 | Y3-C26-COHORT-1-4-7-c5846a2908、Y3-C26-COHORT-1-4-7-3131994926 |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

cohort_month=7, learning_cycle_index=26, year_index=3 命中 2 个候选流程，无法安全选择唯一 flow。

**Codex 建议**

人工对照候选原始文件，确认唯一流程；若只是重复文件，确认后再做 alias/去重标准化。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 吴越一班 | — | 2026-07 | 26 | 2028-08 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：【2026版】第3学年班会+小组会流程/【2026版】第3学年班会+小组会流程/【第26个月】（2026版1、4、7月开班）《哲学手册编制2》视频学习+研讨+经验分析会检视发表+三大委落地经验和案例分享+人生&经营哲学发表+小组学习会-经营分析会观摩+哲学手册编制研讨+三大委案例分享辅导0828.docx
- 原始文件：【2026版】第3学年班会+小组会流程/【2026版】第3学年班会+小组会流程/【第26个月】（2026版1、4、7月开班）《哲学手册编制2》视频学习+研讨+经验分析会检视发表+三大委落地经验和案例分享+人生&经营哲学发表+小组学习会-经营分析会观摩+哲学手册编制研讨0828.docx
- 原始文件：第三年学习计划（2026版20250804）.xlsx
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第1步：经营分析会实操观摩
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第2步：企业参访、企业经营者分享；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第3步：近期读书打卡分享情况；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第4步：上月班级学习会课后作业的检视、分享、辅导；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第5步：哲学手册编写行动计划的检视+研讨；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第6步：三大委建立及运行经验总结分享稿编写的辅导；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第7步：近期重点工作沟通交流；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第8步：空巴。
- `Y3-C26-COHORT-1-4-7-3131994926` 第1步：经营分析会实操观摩
- `Y3-C26-COHORT-1-4-7-3131994926` 第2步：企业参访、企业经营者分享；
- `Y3-C26-COHORT-1-4-7-3131994926` 第3步：近期读书打卡分享情况；
- `Y3-C26-COHORT-1-4-7-3131994926` 第4步：上月班级学习会课后作业的检视、分享、辅导；
- `Y3-C26-COHORT-1-4-7-3131994926` 第5步：哲学手册编写行动计划的检视+研讨；
- `Y3-C26-COHORT-1-4-7-3131994926` 第6步：三大委建立及运行经验总结分享稿编写的辅导；
- `Y3-C26-COHORT-1-4-7-3131994926` 第7步：近期重点工作沟通交流；
- `Y3-C26-COHORT-1-4-7-3131994926` 第8步：空巴。

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

### c0-mapping_missing-7-32-c3b597a0 · MAPPING_MISSING · 7月模板 / 第32周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 7月开班模板 |
| learning_cycle_index | 32 |
| flow_key | — |
| 候选流程 |  |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

按 cohort_month + learning_cycle_index + year_index 未找到对应的小组学习会流程；不能用自然月份或相邻周期猜测。

**Codex 建议**

人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 吴越一班 | — | 2026-07 | 32 | 2029-02 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：第三年学习计划（2026版20250804）.xlsx
- 原始流程步骤：未找到对应流程；以下计划表任务仅作为待核对线索。
- [计划表] 小组学习会：全天或半天
- [计划表] 小组学习会：经营分析会实操观摩
- [计划表] 小组学习会：上月班级学习会的作业检视
- [计划表] 小组学习会：哲学手册编制检视+研讨

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

### c0-mapping_missing-7-33-b919d5f4 · MAPPING_MISSING · 7月模板 / 第33周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 7月开班模板 |
| learning_cycle_index | 33 |
| flow_key | — |
| 候选流程 |  |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

按 cohort_month + learning_cycle_index + year_index 未找到对应的小组学习会流程；不能用自然月份或相邻周期猜测。

**Codex 建议**

人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 吴越一班 | — | 2026-07 | 33 | 2029-03 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：第三年学习计划（2026版20250804）.xlsx
- 原始流程步骤：未找到对应流程；以下计划表任务仅作为待核对线索。
- [计划表] 小组学习会：全天或半天
- [计划表] 小组学习会：经营分析会实操观摩
- [计划表] 小组学习会：上月班级学习会的作业检视
- [计划表] 小组学习会：人财培养体系研讨；（如线上研讨，小组学习会可改为半天）

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

### c0-mapping_missing-7-34-d52145ac · MAPPING_MISSING · 7月模板 / 第34周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 7月开班模板 |
| learning_cycle_index | 34 |
| flow_key | — |
| 候选流程 |  |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

按 cohort_month + learning_cycle_index + year_index 未找到对应的小组学习会流程；不能用自然月份或相邻周期猜测。

**Codex 建议**

人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 吴越一班 | — | 2026-07 | 34 | 2029-04 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：第三年学习计划（2026版20250804）.xlsx
- 原始流程步骤：未找到对应流程；以下计划表任务仅作为待核对线索。
- [计划表] 小组学习会：全天或半天
- [计划表] 小组学习会：经营分析会实操观摩
- [计划表] 小组学习会：上月班级学习会的作业检视
- [计划表] 小组学习会：人财培养体系研讨；（如线上研讨，小组学习会可改为半天）

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

### c0-mapping_conflict-7-26-b3af5cdb · MAPPING_CONFLICT · 7月模板 / 第26周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 7月开班模板 |
| learning_cycle_index | 26 |
| flow_key | — |
| 候选流程 | Y3-C26-COHORT-1-4-7-c5846a2908、Y3-C26-COHORT-1-4-7-3131994926 |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

cohort_month=7, learning_cycle_index=26, year_index=3 命中 2 个候选流程，无法安全选择唯一 flow。

**Codex 建议**

人工对照候选原始文件，确认唯一流程；若只是重复文件，确认后再做 alias/去重标准化。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 圆融五班 | — | 2026-07 | 26 | 2028-08 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：【2026版】第3学年班会+小组会流程/【2026版】第3学年班会+小组会流程/【第26个月】（2026版1、4、7月开班）《哲学手册编制2》视频学习+研讨+经验分析会检视发表+三大委落地经验和案例分享+人生&经营哲学发表+小组学习会-经营分析会观摩+哲学手册编制研讨+三大委案例分享辅导0828.docx
- 原始文件：【2026版】第3学年班会+小组会流程/【2026版】第3学年班会+小组会流程/【第26个月】（2026版1、4、7月开班）《哲学手册编制2》视频学习+研讨+经验分析会检视发表+三大委落地经验和案例分享+人生&经营哲学发表+小组学习会-经营分析会观摩+哲学手册编制研讨0828.docx
- 原始文件：第三年学习计划（2026版20250804）.xlsx
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第1步：经营分析会实操观摩
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第2步：企业参访、企业经营者分享；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第3步：近期读书打卡分享情况；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第4步：上月班级学习会课后作业的检视、分享、辅导；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第5步：哲学手册编写行动计划的检视+研讨；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第6步：三大委建立及运行经验总结分享稿编写的辅导；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第7步：近期重点工作沟通交流；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第8步：空巴。
- `Y3-C26-COHORT-1-4-7-3131994926` 第1步：经营分析会实操观摩
- `Y3-C26-COHORT-1-4-7-3131994926` 第2步：企业参访、企业经营者分享；
- `Y3-C26-COHORT-1-4-7-3131994926` 第3步：近期读书打卡分享情况；
- `Y3-C26-COHORT-1-4-7-3131994926` 第4步：上月班级学习会课后作业的检视、分享、辅导；
- `Y3-C26-COHORT-1-4-7-3131994926` 第5步：哲学手册编写行动计划的检视+研讨；
- `Y3-C26-COHORT-1-4-7-3131994926` 第6步：三大委建立及运行经验总结分享稿编写的辅导；
- `Y3-C26-COHORT-1-4-7-3131994926` 第7步：近期重点工作沟通交流；
- `Y3-C26-COHORT-1-4-7-3131994926` 第8步：空巴。

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

### c0-mapping_missing-7-32-d1731f57 · MAPPING_MISSING · 7月模板 / 第32周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 7月开班模板 |
| learning_cycle_index | 32 |
| flow_key | — |
| 候选流程 |  |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

按 cohort_month + learning_cycle_index + year_index 未找到对应的小组学习会流程；不能用自然月份或相邻周期猜测。

**Codex 建议**

人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 圆融五班 | — | 2026-07 | 32 | 2029-02 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：第三年学习计划（2026版20250804）.xlsx
- 原始流程步骤：未找到对应流程；以下计划表任务仅作为待核对线索。
- [计划表] 小组学习会：全天或半天
- [计划表] 小组学习会：经营分析会实操观摩
- [计划表] 小组学习会：上月班级学习会的作业检视
- [计划表] 小组学习会：哲学手册编制检视+研讨

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

### c0-mapping_missing-7-33-7bf18e98 · MAPPING_MISSING · 7月模板 / 第33周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 7月开班模板 |
| learning_cycle_index | 33 |
| flow_key | — |
| 候选流程 |  |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

按 cohort_month + learning_cycle_index + year_index 未找到对应的小组学习会流程；不能用自然月份或相邻周期猜测。

**Codex 建议**

人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 圆融五班 | — | 2026-07 | 33 | 2029-03 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：第三年学习计划（2026版20250804）.xlsx
- 原始流程步骤：未找到对应流程；以下计划表任务仅作为待核对线索。
- [计划表] 小组学习会：全天或半天
- [计划表] 小组学习会：经营分析会实操观摩
- [计划表] 小组学习会：上月班级学习会的作业检视
- [计划表] 小组学习会：人财培养体系研讨；（如线上研讨，小组学习会可改为半天）

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

### c0-mapping_missing-7-34-3d4eef64 · MAPPING_MISSING · 7月模板 / 第34周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 7月开班模板 |
| learning_cycle_index | 34 |
| flow_key | — |
| 候选流程 |  |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

按 cohort_month + learning_cycle_index + year_index 未找到对应的小组学习会流程；不能用自然月份或相邻周期猜测。

**Codex 建议**

人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 圆融五班 | — | 2026-07 | 34 | 2029-04 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：第三年学习计划（2026版20250804）.xlsx
- 原始流程步骤：未找到对应流程；以下计划表任务仅作为待核对线索。
- [计划表] 小组学习会：全天或半天
- [计划表] 小组学习会：经营分析会实操观摩
- [计划表] 小组学习会：上月班级学习会的作业检视
- [计划表] 小组学习会：人财培养体系研讨；（如线上研讨，小组学习会可改为半天）

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

### c0-mapping_conflict-7-26-4d819cf3 · MAPPING_CONFLICT · 7月模板 / 第26周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 7月开班模板 |
| learning_cycle_index | 26 |
| flow_key | — |
| 候选流程 | Y3-C26-COHORT-1-4-7-c5846a2908、Y3-C26-COHORT-1-4-7-3131994926 |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

cohort_month=7, learning_cycle_index=26, year_index=3 命中 2 个候选流程，无法安全选择唯一 flow。

**Codex 建议**

人工对照候选原始文件，确认唯一流程；若只是重复文件，确认后再做 alias/去重标准化。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 吴越三班 | — | 2026-07 | 26 | 2028-08 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：【2026版】第3学年班会+小组会流程/【2026版】第3学年班会+小组会流程/【第26个月】（2026版1、4、7月开班）《哲学手册编制2》视频学习+研讨+经验分析会检视发表+三大委落地经验和案例分享+人生&经营哲学发表+小组学习会-经营分析会观摩+哲学手册编制研讨+三大委案例分享辅导0828.docx
- 原始文件：【2026版】第3学年班会+小组会流程/【2026版】第3学年班会+小组会流程/【第26个月】（2026版1、4、7月开班）《哲学手册编制2》视频学习+研讨+经验分析会检视发表+三大委落地经验和案例分享+人生&经营哲学发表+小组学习会-经营分析会观摩+哲学手册编制研讨0828.docx
- 原始文件：第三年学习计划（2026版20250804）.xlsx
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第1步：经营分析会实操观摩
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第2步：企业参访、企业经营者分享；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第3步：近期读书打卡分享情况；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第4步：上月班级学习会课后作业的检视、分享、辅导；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第5步：哲学手册编写行动计划的检视+研讨；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第6步：三大委建立及运行经验总结分享稿编写的辅导；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第7步：近期重点工作沟通交流；
- `Y3-C26-COHORT-1-4-7-c5846a2908` 第8步：空巴。
- `Y3-C26-COHORT-1-4-7-3131994926` 第1步：经营分析会实操观摩
- `Y3-C26-COHORT-1-4-7-3131994926` 第2步：企业参访、企业经营者分享；
- `Y3-C26-COHORT-1-4-7-3131994926` 第3步：近期读书打卡分享情况；
- `Y3-C26-COHORT-1-4-7-3131994926` 第4步：上月班级学习会课后作业的检视、分享、辅导；
- `Y3-C26-COHORT-1-4-7-3131994926` 第5步：哲学手册编写行动计划的检视+研讨；
- `Y3-C26-COHORT-1-4-7-3131994926` 第6步：三大委建立及运行经验总结分享稿编写的辅导；
- `Y3-C26-COHORT-1-4-7-3131994926` 第7步：近期重点工作沟通交流；
- `Y3-C26-COHORT-1-4-7-3131994926` 第8步：空巴。

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

### c0-mapping_missing-7-32-98e23dee · MAPPING_MISSING · 7月模板 / 第32周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 7月开班模板 |
| learning_cycle_index | 32 |
| flow_key | — |
| 候选流程 |  |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

按 cohort_month + learning_cycle_index + year_index 未找到对应的小组学习会流程；不能用自然月份或相邻周期猜测。

**Codex 建议**

人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 吴越三班 | — | 2026-07 | 32 | 2029-02 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：第三年学习计划（2026版20250804）.xlsx
- 原始流程步骤：未找到对应流程；以下计划表任务仅作为待核对线索。
- [计划表] 小组学习会：全天或半天
- [计划表] 小组学习会：经营分析会实操观摩
- [计划表] 小组学习会：上月班级学习会的作业检视
- [计划表] 小组学习会：哲学手册编制检视+研讨

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

### c0-mapping_missing-7-33-aece74c9 · MAPPING_MISSING · 7月模板 / 第33周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 7月开班模板 |
| learning_cycle_index | 33 |
| flow_key | — |
| 候选流程 |  |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

按 cohort_month + learning_cycle_index + year_index 未找到对应的小组学习会流程；不能用自然月份或相邻周期猜测。

**Codex 建议**

人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 吴越三班 | — | 2026-07 | 33 | 2029-03 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：第三年学习计划（2026版20250804）.xlsx
- 原始流程步骤：未找到对应流程；以下计划表任务仅作为待核对线索。
- [计划表] 小组学习会：全天或半天
- [计划表] 小组学习会：经营分析会实操观摩
- [计划表] 小组学习会：上月班级学习会的作业检视
- [计划表] 小组学习会：人财培养体系研讨；（如线上研讨，小组学习会可改为半天）

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

### c0-mapping_missing-7-34-a745ffa0 · MAPPING_MISSING · 7月模板 / 第34周期

| 字段 | 当前审计结果 |
|---|---|
| 模板 | 7月开班模板 |
| learning_cycle_index | 34 |
| flow_key | — |
| 候选流程 |  |
| 当前识别的 task_type | REVIEW_REQUIRED |
| task_type 候选 | PRACTICE、DISCUSSION |
| 是否 required | True |
| qr_refs |  |
| 当前 learning content title |  |
| 候选标准课程 |  |
| 当前 credit_rule |  |
| 候选 credit_rule |  |
| 候选积分 |  |
| 优先级 | P1 |
| 影响判断 | 纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供 |
| 是否可以安全自动修复 | 否 |
| 是否必须人工确认 | 是 |

**产生冲突/缺失的技术原因**

按 cohort_month + learning_cycle_index + year_index 未找到对应的小组学习会流程；不能用自然月份或相邻周期猜测。

**Codex 建议**

人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。

#### 真实班级影响

| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |
|---|---|---|---:|---|---:|---|---|
| 吴越三班 | — | 2026-07 | 34 | 2029-04 | — | NOT_PROVIDED | P1 |

#### 原始文件与步骤

- 原始文件：第三年学习计划（2026版20250804）.xlsx
- 原始流程步骤：未找到对应流程；以下计划表任务仅作为待核对线索。
- [计划表] 小组学习会：全天或半天
- [计划表] 小组学习会：经营分析会实操观摩
- [计划表] 小组学习会：上月班级学习会的作业检视
- [计划表] 小组学习会：人财培养体系研讨；（如线上研讨，小组学习会可改为半天）

#### 业务确认（请填写）

- 最终标准内容名称：
- 是否属于本周期小组学习会：是 / 否
- 是否必学：是 / 否
- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER
- 是否有积分：是 / 否 / 待确认
- 如有积分，标准 credit_rule_key：
- 积分值：
- 二维码定位：仅访问入口 / 无二维码 / 不适用
- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料
- 备注：

#### B2 周期顺延不变量

班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

## 附录 A：模板层补充异常（不计入主清单 15 项）

这些项仍然需要业务确认，但当前没有纳入范围的真实班级影响，因此统一为 P2。

| ID | 模板 | 周期 | 状态 | 影响 | 优先级 |
|---|---|---:|---|---|---|
| c0-mapping_conflict-1-26-1422b56d | 1月开班模板 | 26 | MAPPING_CONFLICT | 无当前纳入范围班级 | P2 |
| c0-mapping_missing-1-28-fa52fdee | 1月开班模板 | 28 | MAPPING_MISSING | 无当前纳入范围班级 | P2 |
| c0-mapping_missing-1-29-9d10ec25 | 1月开班模板 | 29 | MAPPING_MISSING | 无当前纳入范围班级 | P2 |
| c0-mapping_conflict-10-25-fa9ee82e | 10月开班模板 | 25 | MAPPING_CONFLICT | 无当前纳入范围班级 | P2 |
| c0-mapping_conflict-10-26-62c9250c | 10月开班模板 | 26 | MAPPING_CONFLICT | 无当前纳入范围班级 | P2 |
| c0-mapping_missing-10-30-c2ea25fa | 10月开班模板 | 30 | MAPPING_MISSING | 无当前纳入范围班级 | P2 |
| c0-mapping_missing-10-31-3bd9d904 | 10月开班模板 | 31 | MAPPING_MISSING | 无当前纳入范围班级 | P2 |
| c0-mapping_missing-10-32-915fb996 | 10月开班模板 | 32 | MAPPING_MISSING | 无当前纳入范围班级 | P2 |

## 附录 B：无二维码必学视频复核

二维码为空不代表内容不存在，也不自动生成积分。

| cohort_month | learning_cycle_index | 标题 | required | qr_refs | credit_rule | 积分 | match_status |
|---:|---:|---|---|---|---|---:|---|
| 1 | 5 | 关于核算表分析&任务单的制作 | True |  | Y1-ACCOUNTING-ANALYSIS-TASK | 40 | MAPPED |
| 1 | 8 | 成功方程式49天讲解 | True |  | — | — | MAPPED |
| 1 | 17 | 阿米巴经营之如何制作分部门核算表 | True |  | — | — | MAPPED |
| 1 | 20 | 优秀改善创新案例分享 | True |  | — | — | MAPPED |
| 1 | 36 | 志愿者学长培训 | True |  | — | — | MAPPED |
| 4 | 5 | 关于核算表分析&任务单的制作 | True |  | Y1-ACCOUNTING-ANALYSIS-TASK | 40 | MAPPED |
| 4 | 10 | 成功方程式49天讲解 | True |  | — | — | MAPPED |
| 4 | 11 | 成功方程式49天讲解 | True |  | — | — | MAPPED |
| 4 | 17 | 阿米巴经营之如何制作分部门核算表 | True |  | — | — | MAPPED |
| 4 | 36 | 志愿者学长培训 | True |  | — | — | MAPPED |
| 7 | 5 | 关于核算表分析&任务单的制作 | True |  | Y1-ACCOUNTING-ANALYSIS-TASK | 40 | MAPPED |
| 7 | 8 | 成功方程式49天讲解 | True |  | — | — | MAPPED |
| 7 | 19 | 阿米巴经营之如何制作分部门核算表 | True |  | — | — | MAPPED |
| 7 | 36 | 志愿者学长培训 | True |  | — | — | MAPPED |
| 10 | 5 | 关于核算表分析&任务单的制作 | True |  | Y1-ACCOUNTING-ANALYSIS-TASK | 40 | MAPPED |
| 10 | 10 | 成功方程式49天讲解 | True |  | — | — | MAPPED |
| 10 | 11 | 成功方程式49天讲解 | True |  | — | — | MAPPED |
| 10 | 15 | 学习践行委讲解 | True |  | — | — | MAPPED |
| 10 | 17 | 改善创新委讲解 | True |  | — | — | MAPPED |
| 10 | 17 | 改善创新优秀企业践行分享 | True |  | — | — | MAPPED |
| 10 | 19 | 阿米巴经营之如何制作分部门核算表 | True |  | — | — | MAPPED |
| 10 | 36 | 志愿者学长培训 | True |  | — | — | MAPPED |

## 附录 C：历史二维码节点审核

本附录暂不替任何二维码节点作 A/B/C 判断，全部保持 D：无法确认，等待业务逐项确认。

| node_id | 模板 | 周期 | 原始文件 | 原始步骤全文 | 二维码定位 | 候选规则 | 当前结论 |
|---|---|---:|---|---|---|---|---|
| Y2-C14-COHORT-1-4-7-10-885bae980e-course-node-1 | 1、4、7、10 | 14 | 【第14个月】（2026版1、4、7、10月开班）班级学习会-《如何召开经营分析会-基础班+进阶版》+经营分析会实操+人生&经营哲学践行发表+小组学习会-幸福关爱委视频（企业年度计划修正）0814.docx | 【幸福关爱委讲解】视频学习+研讨； | {"media_target": "media/image3.png", "qr_url_present": false, "relationship_id": "rId6", "source_paragraph_index": 42} |  | D. 无法确认（待业务核对）|
| Y2-C14-COHORT-1-4-7-10-885bae980e-course-node-2 | 1、4、7、10 | 14 | 【第14个月】（2026版1、4、7、10月开班）班级学习会-《如何召开经营分析会-基础班+进阶版》+经营分析会实操+人生&经营哲学践行发表+小组学习会-幸福关爱委视频（企业年度计划修正）0814.docx | 【幸福关爱委讲解】视频学习+研讨； | {"media_target": "media/image4.png", "qr_url_present": false, "relationship_id": "rId7", "source_paragraph_index": 42} |  | D. 无法确认（待业务核对）|
| Y2-C15-COHORT-1-4-7-21a2178ede-course-node-1 | 1、4、7 | 15 | 【第15个月】（2026版1、4、7月开班）班级学习会-《如何改善创新》+经营分析会检视+人生&经营哲学发表+经营分析会研讨+小组学习会-经营分析会实操观摩+《改善创新委》视频学习0814.docx | 【改善创新委讲解】、【改善创新优秀企业践行分享】视频学习+研讨； | {"media_target": "media/image4.jpeg", "qr_url_present": false, "relationship_id": "rId10", "source_paragraph_index": 17} |  | D. 无法确认（待业务核对）|
| Y2-C15-COHORT-1-4-7-21a2178ede-course-node-2 | 1、4、7 | 15 | 【第15个月】（2026版1、4、7月开班）班级学习会-《如何改善创新》+经营分析会检视+人生&经营哲学发表+经营分析会研讨+小组学习会-经营分析会实操观摩+《改善创新委》视频学习0814.docx | 【改善创新委讲解】、【改善创新优秀企业践行分享】视频学习+研讨； | {"media_target": "media/image1.png", "qr_url_present": false, "relationship_id": "rId7", "source_paragraph_index": 17} |  | D. 无法确认（待业务核对）|
| Y2-C16-COHORT-10-e89ea06de1-course-node-1 | 10 | 16 | 【第16个月】（2026版10月开班）班级学习会-《干法》+《京瓷哲学》读书总结+经营分析会检视+研讨+小组学习会-经营分析会实操观摩+《幸福关爱委讲解》视频学习0814.docx | 【幸福关爱委讲解】视频学习+研讨；<br>体系讲解 企业案例分享 | {"media_target": "media/image2.png", "qr_url_present": false, "relationship_id": "rId8", "source_paragraph_index": 13} |  | D. 无法确认（待业务核对）|
| Y2-C16-COHORT-10-e89ea06de1-course-node-2 | 10 | 16 | 【第16个月】（2026版10月开班）班级学习会-《干法》+《京瓷哲学》读书总结+经营分析会检视+研讨+小组学习会-经营分析会实操观摩+《幸福关爱委讲解》视频学习0814.docx | 【幸福关爱委讲解】视频学习+研讨；<br>体系讲解 企业案例分享 | {"media_target": "media/image3.png", "qr_url_present": false, "relationship_id": "rId9", "source_paragraph_index": 13} |  | D. 无法确认（待业务核对）|
| Y2-C16-COHORT-1-4-da8410ed23-course-node-1 | 1、4 | 16 | 【第16个月】（2026版1、4月开班）班级学习会-《阿米巴经营带来企业持续发展》+学习践行委落地优秀案例发表+经营分析会检视+研讨+小组学习会-经营分析会实操观摩+《阿米巴经营之概论》视频学习+研讨+学习践行发表辅导0814.docx | 【阿米巴经营之概论】视频学习+研讨； | {"media_target": "media/image1.jpeg", "qr_url_present": false, "relationship_id": "rId7", "source_paragraph_index": 11} |  | D. 无法确认（待业务核对）|
| Y2-C18-COHORT-7-10-bcbc9e3d33-course-node-1 | 7、10 | 18 | 【第18个月】（2026版7、10月开班）班级学习会-《阿米巴经营带来企业持续发展》+学习践行委落地优秀案例发表+经营分析会检视+研讨+小组学习会-经营分析会实操观摩+《阿米巴经营之概论》视频学习+研讨+学习践行发表辅导0818.docx | 【阿米巴经营之概论】视频学习+研讨； | {"media_target": "media/image1.jpeg", "qr_url_present": false, "relationship_id": "rId4", "source_paragraph_index": 11} |  | D. 无法确认（待业务核对）|
| Y2-C21-COHORT-4-7-10-9dc872e110-course-node-1 | 4、7、10 | 21 | 【第21个月】（2026版4、7、10月开班）班级学习会-《企业的自我革新-从京瓷的新产品开发谈起》+改善创新案例总结发表+经营分析会检视+研讨+小组学习会-经营分析会实操+《优秀改善创新案例分享》视频学习+研讨+改善创新案例总结发表辅导0819.docx | 【优秀改善创新案例分享】视频学习+研讨 | {"media_target": "media/image1.png", "qr_url_present": false, "relationship_id": "rId7", "source_paragraph_index": 13} |  | D. 无法确认（待业务核对）|
| Y3-C27-COHORT-7-ba27a4dd0d-course-node-1 | 7 | 27 | 【第27个月】（2026版7月开班）百日奋战启动会+经营分析会检视发表+三大委落地经验分享+人生&经营哲学发表+小组学习会-经营分析会观摩+《百日奋战》视频学习+哲学手册编制研讨0912.docx | 《百日奋战》讲解视频，启动会筹备和研讨； | {"media_target": "media/image1.png", "qr_url_present": false, "relationship_id": "rId6", "source_paragraph_index": 19} |  | D. 无法确认（待业务核对）|
| Y3-C27-COHORT-7-ba27a4dd0d-course-node-2 | 7 | 27 | 【第27个月】（2026版7月开班）百日奋战启动会+经营分析会检视发表+三大委落地经验分享+人生&经营哲学发表+小组学习会-经营分析会观摩+《百日奋战》视频学习+哲学手册编制研讨0912.docx | 《百日奋战》讲解视频，启动会筹备和研讨； | {"media_target": "media/image2.png", "qr_url_present": false, "relationship_id": "rId7", "source_paragraph_index": 19} |  | D. 无法确认（待业务核对）|
| Y3-C30-COHORT-1-4-b2f6700018-course-node-1 | 1、4 | 30 | 【第30个月】（2026版1、4月开班）百日奋战启动会+经营分析会检视发表+三大委落地经验分享+小组学习会-经营分析会观摩+《百日奋战》视频学习0922.docx | 《百日奋战》讲解视频，启动会筹备和研讨； | {"media_target": "media/image1.png", "qr_url_present": false, "relationship_id": "rId6", "source_paragraph_index": 19} |  | D. 无法确认（待业务核对）|
| Y3-C30-COHORT-1-4-b2f6700018-course-node-2 | 1、4 | 30 | 【第30个月】（2026版1、4月开班）百日奋战启动会+经营分析会检视发表+三大委落地经验分享+小组学习会-经营分析会观摩+《百日奋战》视频学习0922.docx | 《百日奋战》讲解视频，启动会筹备和研讨； | {"media_target": "media/image2.png", "qr_url_present": false, "relationship_id": "rId7", "source_paragraph_index": 19} |  | D. 无法确认（待业务核对）|
| Y3-C33-COHORT-10-4c4d3a8016-course-node-1 | 10 | 33 | 【第33个月】（2026版10月开班）百日奋战启动会+经营分析会检视发表+三大委落地经验分享+小组学习会-经营分析会观摩+《百日奋战》视频学习0915.docx | 《百日奋战》讲解视频，启动会筹备和研讨； | {"media_target": "media/image1.png", "qr_url_present": false, "relationship_id": "rId6", "source_paragraph_index": 19} |  | D. 无法确认（待业务核对）|
| Y3-C33-COHORT-10-4c4d3a8016-course-node-2 | 10 | 33 | 【第33个月】（2026版10月开班）百日奋战启动会+经营分析会检视发表+三大委落地经验分享+小组学习会-经营分析会观摩+《百日奋战》视频学习0915.docx | 《百日奋战》讲解视频，启动会筹备和研讨； | {"media_target": "media/image2.png", "qr_url_present": false, "relationship_id": "rId7", "source_paragraph_index": 19} |  | D. 无法确认（待业务核对）|

## 业务确认后才能进入下一步

本文件完成后停止。只有业务逐项确认内容、必学属性、二维码定位和积分规则，才能生成下一版正式 mapping；本轮不开始 V1.3-C 小程序页面开发。
