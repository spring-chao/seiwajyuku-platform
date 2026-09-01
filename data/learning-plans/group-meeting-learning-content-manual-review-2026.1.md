# V1.3-C0 小组学习会学习内容映射人工审核清单

> 状态：PENDING_BUSINESS_REVIEW。本文档只提供证据和待确认项，不写入正式 mapping、不赋予正式积分。

## 审核口径

- 基线 commit：`3a8e7afcd62ddda2030cbef8043eca36cc80de6d`
- 主清单采用最新真实班级投影口径：0 个冲突 + 0 个缺失 = 0 个审核项。
- 主清单只覆盖当前纳入小组学习会 36 周期计划的真实班级；模板层但暂无真实班级影响的异常放入补充附录。
- 1/26 等写法统一解释为 `cohort_month=1, learning_cycle_index=26`，不使用自然月份替代学习周期。
- 实际周期状态来自 `class_learning_cycles`；当前审计的实际运行状态为 `NOT_PROVIDED`，没有虚构 OPEN 周期。
- 班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。

## 汇总

| 项目 | 数量 |
|---|---:|
| 144 模板周期 | 144 |
| MATCHED | 144 |
| 主清单 MAPPING_CONFLICT | 0 |
| 主清单 MAPPING_MISSING | 0 |
| 主清单审核项 | 0 |
| 模板补充项 | 5 |
| 可安全自动修复 | 0 |
| 全部 REVIEW_REQUIRED | 5 |
| P0 / P1 / P2 | 0 / 0 / 5 |
| 无二维码必学视频投影 | 22 |
| 历史二维码节点 | 7 |

### 模板层计数说明

仓库全模板 mapping 文件当前仍有 2 个冲突 + 3 个缺失；这些是当前纳入范围之外的模板项，不计入主清单，也不删除，见补充附录。

## 主清单汇总表

| ID | 模板 | 周期 | 状态 | 原内容 | 候选规则 | 影响班级 | 优先级 | Codex建议 | 是否人工确认 |
|---|---|---:|---|---|---|---|---|---|---|

## 0 项主清单逐项证据与业务确认

## 附录 A：模板层补充异常（不计入主清单 0 项）

这些项仍然需要业务确认，但当前没有纳入范围的真实班级影响，因此统一为 P2。

| ID | 模板 | 周期 | 状态 | 影响 | 优先级 |
|---|---|---:|---|---|---|
| c0-mapping_conflict-10-25-976f5408 | 10月开班模板 | 25 | MAPPING_CONFLICT | 无当前纳入范围班级 | P2 |
| c0-mapping_conflict-10-26-f7d5870c | 10月开班模板 | 26 | MAPPING_CONFLICT | 无当前纳入范围班级 | P2 |
| c0-mapping_missing-10-30-e3df8778 | 10月开班模板 | 30 | MAPPING_MISSING | 无当前纳入范围班级 | P2 |
| c0-mapping_missing-10-31-5238a59f | 10月开班模板 | 31 | MAPPING_MISSING | 无当前纳入范围班级 | P2 |
| c0-mapping_missing-10-32-abf94976 | 10月开班模板 | 32 | MAPPING_MISSING | 无当前纳入范围班级 | P2 |

## 附录 B：无二维码必学视频复核

二维码为空不代表内容不存在，也不自动生成积分。

| cohort_month | learning_cycle_index | 标题 | required | qr_refs | credit_rule | 积分 | match_status |
|---:|---:|---|---|---|---|---:|---|
| 1 | 5 | 关于核算表分析&任务单的制作 | True |  | Y1-ACCOUNTING-ANALYSIS-TASK | 40 | MAPPED |
| 1 | 8 | 成功方程式49天讲解 | True |  | — | — | MAPPED |
| 1 | 17 | 阿米巴经营之如何制作分部门核算表 | True |  | GM-AMOEBA-DEPARTMENT-ACCOUNTING | 20 | MAPPED |
| 1 | 20 | 优秀改善创新案例分享 | True |  | — | — | MAPPED |
| 1 | 36 | 志愿者学长培训 | True |  | — | — | MAPPED |
| 4 | 5 | 关于核算表分析&任务单的制作 | True |  | Y1-ACCOUNTING-ANALYSIS-TASK | 40 | MAPPED |
| 4 | 10 | 成功方程式49天讲解 | True |  | — | — | MAPPED |
| 4 | 11 | 成功方程式49天讲解 | True |  | — | — | MAPPED |
| 4 | 17 | 阿米巴经营之如何制作分部门核算表 | True |  | GM-AMOEBA-DEPARTMENT-ACCOUNTING | 20 | MAPPED |
| 4 | 36 | 志愿者学长培训 | True |  | — | — | MAPPED |
| 7 | 5 | 关于核算表分析&任务单的制作 | True |  | Y1-ACCOUNTING-ANALYSIS-TASK | 40 | MAPPED |
| 7 | 8 | 成功方程式49天讲解 | True |  | — | — | MAPPED |
| 7 | 19 | 阿米巴经营之如何制作分部门核算表 | True |  | GM-AMOEBA-DEPARTMENT-ACCOUNTING | 20 | MAPPED |
| 7 | 36 | 志愿者学长培训 | True |  | — | — | MAPPED |
| 10 | 5 | 关于核算表分析&任务单的制作 | True |  | Y1-ACCOUNTING-ANALYSIS-TASK | 40 | MAPPED |
| 10 | 10 | 成功方程式49天讲解 | True |  | — | — | MAPPED |
| 10 | 11 | 成功方程式49天讲解 | True |  | — | — | MAPPED |
| 10 | 15 | 学习践行委讲解 | True |  | — | — | MAPPED |
| 10 | 17 | 改善创新委讲解 | True |  | — | — | MAPPED |
| 10 | 17 | 改善创新优秀企业践行分享 | True |  | — | — | MAPPED |
| 10 | 19 | 阿米巴经营之如何制作分部门核算表 | True |  | GM-AMOEBA-DEPARTMENT-ACCOUNTING | 20 | MAPPED |
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
| Y2-C21-COHORT-4-7-10-9dc872e110-course-node-1 | 4、7、10 | 21 | 【第21个月】（2026版4、7、10月开班）班级学习会-《企业的自我革新-从京瓷的新产品开发谈起》+改善创新案例总结发表+经营分析会检视+研讨+小组学习会-经营分析会实操+《优秀改善创新案例分享》视频学习+研讨+改善创新案例总结发表辅导0819.docx | 【优秀改善创新案例分享】视频学习+研讨 | {"media_target": "media/image1.png", "qr_url_present": false, "relationship_id": "rId7", "source_paragraph_index": 13} |  | D. 无法确认（待业务核对）|

## 业务确认后才能进入下一步

本文件只保留尚未确认的模板项；已经由业务确认的流程已写入当前 mapping。剩余项目以后有明确资料时再补，本轮不开始 V1.3-C 小程序页面开发。
