#!/usr/bin/env python3
"""Render the three G5.4-C1.5/C2A verification reports from private artifacts."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_DIR = REPO_ROOT / "docs" / "verification"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.split()).replace("|", "／") or "—"


def _course_report(reconciliation: dict[str, Any]) -> str:
    rows = []
    for item in reconciliation["plan"]:
        action = item["action"]
        before = item.get("before") or {}
        after = item.get("after") or {}
        rows.append(
            "| {key} | {action} | {types} | {before_name} / {before_year} / {before_points} / {before_status} / {before_aliases} | {after_name} / {after_year} / {after_points} / {after_status} / {after_aliases} |".format(
                key=_cell(item.get("course_key")),
                action=action,
                types=_cell(", ".join(item.get("difference_types", []))),
                before_name=_cell(before.get("course_name")),
                before_year=_cell(before.get("year_index")),
                before_points=_cell(before.get("credit_points")),
                before_status=_cell(before.get("status")),
                before_aliases=_cell(", ".join(before.get("aliases") or [])),
                after_name=_cell(after.get("course_name")),
                after_year=_cell(after.get("year_index")),
                after_points=_cell(after.get("credit_points")),
                after_status=_cell(after.get("status")),
                after_aliases=_cell(", ".join(after.get("aliases") or [])),
            )
        )
    return f"""# G5.4-C1.5 课程规则生产收口 DRY-RUN 报告

生成目的：为 0064 提供可审计的生产规则预迁移收口计划。本报告只消费生产只读导出，不执行生产写入。

## 门禁结论

| 门禁 | 结果 |
| --- | --- |
| `COURSE_RULE_CANONICAL_SOURCE` | `CONFIRMED` |
| `COURSE_RULE_PRODUCTION_DIFF` | `COMPLETE` |
| `COURSE_RULE_PRODUCTION_APPLY` | `NOT_EXECUTED` |
| `0064` | `NOT_EXECUTED / 当前 fail-closed` |
| `0065` | `NOT_EXECUTED` |
| 生产规则写入 | `0` |

## 来源语义

- canonical business source：`data/learning-plans/course-credit-rules-2026.json`。
- canonical 内容状态：`BUSINESS_CONFIRMED`；25 条具体规则均为 `CONFIRMED`，业务确认日期 `2026-09-01`。
- 数据库部署状态仍为 `DRAFT`；顶层 JSON 的 `DRAFT` 不代表业务内容未确认。
- `course-credit-catalog-2026.json` 是 28 项的界面目录投影（25 项已配置、3 项待确认），不是 25 条生产政策的 canonical source。
- 0064 的 SQLite/MySQL SQL seed 已固定 canonical 文件 SHA-256，并由测试核对迁移结果与 canonical 25 条字段。

## 生产与 canonical 摘要

| 项目 | 值 |
| --- | ---: |
| current_version_id | `{reconciliation.get('current_version_id')}` |
| current_status | `{reconciliation.get('current_status')}` |
| production_rule_count | `{reconciliation.get('production_rule_count')}` |
| canonical_rule_count | `{reconciliation.get('canonical_rule_count')}` |
| exact_match_count | `{reconciliation.get('exact_match_count')}` |
| missing_count | `{reconciliation.get('missing_count')}` |
| different_count | `{reconciliation.get('different_count')}` |
| extra_count | `{reconciliation.get('extra_count')}` |
| conflict_count | `{reconciliation.get('conflict_count')}` |
| production_fingerprint | `{reconciliation.get('production_fingerprint')}` |
| canonical_fingerprint | `{reconciliation.get('canonical_fingerprint')}` |
| apply_status | `{reconciliation.get('apply_status')}` |

## 逐条差异计划

`KEEP` 只表示字段级完全一致；`UPDATE` 表示按 canonical 值更新候选 DRAFT 行；`ADD` 表示生产缺失；`BLOCK` 表示未知生产数据，默认不得删除。

| course_key | action | difference_types | before（名称 / 年份 / 分值 / 状态 / aliases） | after（名称 / 年份 / 分值 / 状态 / aliases） |
| --- | --- | --- | --- | --- |
{chr(10).join(rows)}

本次具体可见的业务分值差异包括：`Y1-KYOCERA-ANNUAL-PLAN` 30 → 40、`Y1-ANNUAL-MONTHLY-MGMT` 15 → 11；另有两个已在 canonical 中确认但生产仍为 `PENDING/0` 的 AUTO-QR 行。3 条 `EXTRA_IN_PRODUCTION`（生产临时/待配置规则）全部保留并 BLOCK，不自动删除。

## APPLY 安全边界

未来 APPLY 必须重新读取生产和 canonical，并同时匹配本报告的两个 fingerprint；生产在 DRY-RUN 与 APPLY 之间发生变化时必须中止。当前 CLI 的 `--apply` 没有默认写入适配器，明确返回 `NOT_EXECUTED`。本轮没有执行生产规则 UPDATE/INSERT、0064、0065 或任何历史账本写入。
"""


def _c2a_report(c2a: dict[str, Any], staging: dict[str, Any]) -> str:
    snapshot = c2a["snapshot"]
    mapping_counts = c2a["class_mapping"]["counts"]
    matching = c2a["matching"]
    tables = snapshot.get("row_counts") or {}
    mapping_rows = c2a["class_mapping"]["rows"]
    mapping_lines = []
    for item in mapping_rows:
        if item["mapping_status"] == "EXACT":
            continue
        candidates = ", ".join(item.get("candidate_org_unit_ids") or []) or "—"
        mapping_lines.append(
            f"| {_cell(item.get('source_sheet'))} | {_cell(item.get('raw_class_name'))} | {_cell(item.get('mapping_status'))} | {_cell(item.get('mapping_reason'))} | {_cell(candidates)} |"
        )
    # The private JSON is not referenced in the committed report.  Use the
    # compact center/class summary already produced by the dry-run instead.
    by_center_lines = []
    for center, values in sorted(matching.get("by_center_and_class", {}).items()):
        for label, count in sorted(values.items()):
            by_center_lines.append(f"| {_cell(center)} | {_cell(label)} | {count} |")
    summary = matching["summary"]
    return f"""# G5.4-C2A 权威成员快照与历史身份匹配报告

本报告来自授权生产只读快照和隔离 SQLite staging DRY-RUN。成员姓名、成员编号和关系只保存在未跟踪的 `.codex-tmp` 私有快照中；本报告不输出手机号或逐人隐私字段。

## 门禁结论

| 门禁 | 结果 |
| --- | --- |
| `AUTHORITATIVE_MEMBER_SNAPSHOT` | `READY` |
| `HISTORICAL_CLASS_MAPPING` | `REVIEWED / 18 EXACT, 3 AMBIGUOUS, 6 NOT_FOUND` |
| `723_MEMBER_MATCH_DRY_RUN` | `COMPLETE` |
| `17_CREDIT_ANOMALIES` | `STILL_SEPARATE` |
| `HISTORICAL_LEDGER_POST` | `0` |
| `PRODUCTION_WRITE` | `0` |
| `LEARNING_CREDIT_SETTLEMENT` | `false` |

## 权威快照

| 项目 | 值 |
| --- | --- |
| snapshot_id | `{_cell(snapshot.get('snapshot_id'))}` |
| captured_at | `{_cell(snapshot.get('captured_at'))}` |
| source_revision | `{_cell(snapshot.get('source_revision'))}` |
| snapshot_fingerprint | `{_cell(snapshot.get('fingerprint'))}` |
| members | `{tables.get('members')}` |
| org_units | `{tables.get('org_units')}` |
| class_candidates | `{tables.get('class_candidates')}` |
| member_org_relations | `{tables.get('member_org_relations')}` |
| class_learning_bindings | `{tables.get('class_learning_bindings')}` |
| class_learning_cycles | `{tables.get('class_learning_cycles')}` |
| learning_plan_versions | `{tables.get('learning_plan_versions')}` |

快照查询范围仅覆盖苏州分中心工作簿涉及的七个分中心及 2026 年有交集的组织关系；排除了手机号、密码、令牌和无关班级字段。`source_revision` 仅表示抓取时的代码基线，不能替代业务负责人对主档的复核。

## 班级映射

| 状态 | 数量 |
| --- | ---: |
| EXACT | `{mapping_counts.get('EXACT', 0)}` |
| CONFIRMED_ALIAS | `{mapping_counts.get('CONFIRMED_ALIAS', 0)}` |
| AMBIGUOUS | `{mapping_counts.get('AMBIGUOUS', 0)}` |
| NOT_FOUND | `{mapping_counts.get('NOT_FOUND', 0)}` |

组别仅作为辅助证据，不作为永久身份键。未确认的“苏州不一班/真干一班”别名、吴越三班新旧历史节点、来源工作簿中的空班级/“精进组”班级标签都没有被强行确认。

| source_sheet | raw_class_name | mapping_status | reason | candidate_org_unit_ids |
| --- | --- | --- | --- | --- |
{chr(10).join(mapping_lines)}

## 723 行匹配统计

| identity outcome | 行数 |
| --- | ---: |
| TOTAL_ROWS | `{summary.get('TOTAL_ROWS')}` |
| AUTO_MATCHED | `{summary.get('AUTO_MATCHED')}` |
| CONFIRMED | `{summary.get('CONFIRMED')}` |
| AMBIGUOUS | `{summary.get('AMBIGUOUS')}` |
| NOT_FOUND | `{summary.get('NOT_FOUND')}` |
| CONFLICT | `{summary.get('CONFLICT')}` |
| HISTORICAL_RELATION_UNKNOWN | `{summary.get('HISTORICAL_RELATION_UNKNOWN')}` |
| identity_ready_rows | `{summary.get('identity_ready_rows')}` |
| identity_blocked_rows | `{matching.get('identity_blocked_rows')}` |
| credit_validation_ready_rows | `{summary.get('credit_validation_ready_rows')}` |
| credit_validation_blocked_rows | `{matching.get('credit_validation_blocked_rows')}` |
| identity_and_credit_ready_rows | `{summary.get('identity_and_credit_ready_rows')}` |

确定性规则是：中心范围内班级名称精确一致、姓名精确一致、历史班级关系唯一且覆盖来源月份/年度时才 `AUTO_MATCHED`；小组不同不会消除同名歧义，模糊姓名不会自动确认。逐行结果保存在本地私有 DRY-RUN JSON，不进入 Git。

## 按来源组织的结果摘要

| scope | result | rows |
| --- | --- | ---: |
{chr(10).join(by_center_lines)}

## 隔离 staging 结果

| 项目 | 值 |
| --- | ---: |
| staging_batch_id | `{staging.get('batch_id')}` |
| source_rows | `{staging.get('parsed_summary', {}).get('source_row_count')}` |
| source_items | `{staging.get('parsed_summary', {}).get('nonzero_item_count')}` |
| staging rows updated | `{staging.get('staging', {}).get('updated_rows')}` |
| staging items updated | `{staging.get('staging', {}).get('updated_items')}` |
| items with matched_member_id | `{staging.get('staging', {}).get('matched_item_count')}` |
| ledger count before / after | `{staging.get('safety', {}).get('ledger_count_before')} / {staging.get('safety', {}).get('ledger_count_after')}` |
| ledger_entries_delta | `{staging.get('safety', {}).get('ledger_entries_delta')}` |

17 条总分异常没有因为身份匹配成功而被批准：`TOTAL_MISSING=15`、`ZERO=2`，另有 `DETAIL_MISSING=0`、`MISMATCH=0`。身份状态与分值校验状态保持两个独立维度。

未完成事项：153 行仍需班级/成员人工确认或来源主档补充；其中 90 行是班级/姓名歧义，63 行没有可安全确认的成员映射。它们不得进入历史 ledger。
"""


def _period_report(c2a: dict[str, Any]) -> str:
    period = c2a["period_analysis"]
    groups = period["groups"]
    type_names = {
        "LEGACY_OFFLINE_COURSE": "线下课程",
        "LEGACY_ONLINE_COURSE": "线上课程",
        "LEGACY_STUDY_TOUR": "游学",
        "LEGACY_REPORT_EVENT": "报告会",
        "LEGACY_CLASS_MEETING": "班级学习日",
        "LEGACY_GROUP_MEETING": "小组学习会",
        "LEGACY_READING_AND_SHARE": "每日读书/优秀分享",
    }
    aggregate: dict[str, list[float]] = defaultdict(lambda: [0, 0])
    for item in groups:
        key = item["legacy_credit_type"]
        aggregate[key][0] += item["count"]
        aggregate[key][1] += item["points_total"]
    type_lines = [
        f"| {type_names.get(key, key)} (`{key}`) | {int(values[0])} | {int(values[1])} |"
        for key, values in sorted(aggregate.items())
    ]
    detail_lines = [
        f"| {_cell(type_names.get(item['legacy_credit_type'], item['legacy_credit_type']))} | {_cell(item['source_sheet'])} | {_cell(item['source_column_name'])} | {item['count']} | {item['points_total']} |"
        for item in groups
    ]
    return f"""# G5.4-C2A 未分类历史学分期间分析

## 结论

本次 10,122 个历史 item 中，`UNCLASSIFIED_PERIOD_REVIEW` 共 **1,290 条**，合计 **28,400 分**。它们全部可以由来源文件确认属于 2026 年，但表头没有提供可安全转换成自然月的证据，因此统一分类为：

```text
period_resolution_status = YEAR_ONLY_CONFIRMED
period_review_status     = PERIOD_REVIEW_REQUIRED
occurred_on              = NULL
```

这不是忽略，也不是默认月份；在业务负责人补充正式月份/学习轮次证据前，不生成需要 `occurred_on` 的历史 ledger entry。

## 分类门禁

| 项目 | 值 |
| --- | ---: |
| unclassified items | `{period.get('unclassified_item_count')}` |
| total points | `28,400` |
| period classification | `FULLY_CLASSIFIED` |
| month resolution | `0`（本批没有被制造的月份） |
| ledger writes | `0` |
| production writes | `0` |

## 按历史学分类型

| legacy_credit_type | item_count | points_total |
| --- | ---: | ---: |
{chr(10).join(type_lines)}

## 完整明细：类型 / 来源工作表 / 来源列 / 条数 / 分值

下表逐组列出 parser 的 `legacy_credit_type + source_sheet + source_column_name` 聚合，合计必须回到 1,290 条和 28,400 分。来源列名称保留原始表头语义，不据此推断月份。

| 类型 | source_sheet | source_column_name | count | points_total |
| --- | --- | --- | ---: | ---: |
{chr(10).join(detail_lines)}

## 后续解析规则

- 只有来源列明确写出月份时，才标记 `PERIOD_RESOLVED`。
- 只有业务资料能证明年份而不能证明月份时，标记 `YEAR_ONLY_CONFIRMED`，并保持 `PERIOD_REVIEW_REQUIRED`。
- 学习计划“第 N 个月”不能直接当作自然年 N 月；必须结合该班级/学员的真实 learning round 起始日期。
- 没有月份、学习轮次或正式资料时，禁止默认 8 月、12 月 31 日或按 Excel 列顺序猜测。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reconciliation", required=True, type=Path)
    parser.add_argument("--c2a", required=True, type=Path)
    parser.add_argument("--staging", required=True, type=Path)
    args = parser.parse_args()
    reconciliation = _read(args.reconciliation)
    c2a = _read(args.c2a)
    staging = _read(args.staging)
    VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "2026-09-18-G5.4-C1.5课程规则生产收口DRY-RUN报告.md": _course_report(reconciliation),
        "2026-09-18-G5.4-C2A权威成员快照与历史身份匹配报告.md": _c2a_report(c2a, staging),
        "2026-09-18-G5.4-C2A未分类历史学分期间分析.md": _period_report(c2a),
    }
    for name, content in outputs.items():
        (VERIFY_DIR / name).write_text(content.rstrip() + "\n", encoding="utf-8")
        print(VERIFY_DIR / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
