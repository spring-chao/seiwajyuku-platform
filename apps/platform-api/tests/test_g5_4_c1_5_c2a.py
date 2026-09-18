from __future__ import annotations

from pathlib import Path

from app.services.course_credit_canonical import (
    canonical_fingerprint,
    load_canonical_policy,
)
from app.services.course_credit_reconciliation import reconcile_course_rules
from app.services.course_credit_reconciliation import guarded_apply
from app.services.historical_credit_import import summarize_period_items
from app.services.historical_credit_matching import (
    build_historical_class_mappings,
    match_historical_rows,
)


def test_canonical_course_policy_is_business_confirmed_and_unique() -> None:
    policy = load_canonical_policy()
    assert policy["content_status"] == "BUSINESS_CONFIRMED"
    assert policy["deployment_status"] == "DRAFT"
    assert len(policy["rules"]) == 25
    assert all(rule["status"] == "CONFIRMED" for rule in policy["rules"])
    assert len(canonical_fingerprint(policy)) == 64


def test_reconciliation_is_field_exact_and_blocks_unknown_production_rows() -> None:
    policy = load_canonical_policy()
    canonical = policy["rules"]
    production = [
        {**canonical[0], "status": "CONFIGURED", "id": 1},
        {
            **canonical[1],
            "status": "CONFIGURED",
            "credit_points": canonical[1]["credit_points"] + 1,
            "id": 2,
        },
        {
            "course_key": "TEMP-UNKNOWN",
            "course_name": "未知临时课程",
            "year_index": 1,
            "credit_points": 9,
            "status": "PENDING",
            "aliases": [],
            "id": 3,
        },
    ]
    result = reconcile_course_rules(
        version={
            "id": 3,
            "plan_key": "STANDARD_3Y_2026",
            "version_label": "2026.1",
            "status": "DRAFT",
            "based_on_version_label": "2026",
        },
        production_rules=production,
        policy=policy,
    )
    assert result["canonical_rule_count"] == 25
    assert result["exact_match_count"] == 1
    assert result["different_count"] == 1
    assert result["missing_count"] == 23
    assert result["extra_count"] == 1
    assert result["conflict_count"] == 0
    assert result["write_performed"] is False
    assert result["apply_status"] == "BLOCKED_BY_EXTRA_OR_CONFLICT"
    assert any(item["action"] == "BLOCK" and item["course_key"] == "TEMP-UNKNOWN" for item in result["plan"])


def test_guarded_apply_requires_fingerprints_and_builds_audit_payload() -> None:
    policy = load_canonical_policy()
    result = reconcile_course_rules(
        version={"id": 3, "status": "DRAFT"},
        production_rules=[{**policy["rules"][0], "status": "CONFIGURED", "id": 1}],
        policy=policy,
    )
    captured: list[dict] = []
    guarded_apply(
        result,
        expected_production_fingerprint=result["production_fingerprint"],
        expected_canonical_fingerprint=result["canonical_fingerprint"],
        actor="test-operator",
        reason="测试规则收口审计",
        reconciliation_batch_id="c1.5-test-001",
        writer=lambda plan: captured.extend(plan),
    )
    assert captured
    audit = captured[0]["audit"]
    assert audit["course_key"] == captured[0]["course_key"]
    assert audit["actor"] == "test-operator"
    assert audit["reason"] == "测试规则收口审计"
    assert audit["reconciliation_batch_id"] == "c1.5-test-001"
    assert audit["timestamp"]


def test_named_unused_placeholders_require_complete_zero_reference_evidence() -> None:
    policy = load_canonical_policy()
    placeholder_rows = [
        {
            "id": 41,
            "course_key": "AUTO-QR-EXCELLENT-IMPROVEMENT",
            "course_name": "优秀改善创新案例分享",
            "aliases": ["优秀改善创新案例分享"],
        },
        {
            "id": 38,
            "course_key": "AUTO-QR-HAPPINESS-CARE",
            "course_name": "幸福关爱委讲解",
            "aliases": ["幸福关爱委"],
        },
        {
            "id": 39,
            "course_key": "AUTO-QR-IMPROVEMENT-INNOVATION",
            "course_name": "改善创新委讲解与案例分享",
            "aliases": ["改善创新委", "改善创新案例"],
        },
    ]
    placeholder_rows = [
        {
            **row,
            "year_index": 2,
            "credit_points": 0,
            "status": "PENDING",
            "source": "SYSTEM_DEFAULT",
            "created_at": "2026-09-01T00:00:00+00:00",
            "updated_at": "2026-09-01T00:00:00+00:00",
        }
        for row in placeholder_rows
    ]
    references = {
        row["course_key"]: {
            "all_reference_count": 0,
            "generic_rule_mapping": 0,
            "course_rule_mapping": 0,
            "study_meeting_course_reference": 0,
            "completion_fact": 0,
            "ledger_reference": 0,
        }
        for row in placeholder_rows
    }
    result = reconcile_course_rules(
        version={"id": 3, "status": "DRAFT"},
        production_rules=placeholder_rows,
        policy=policy,
        reference_counts=references,
    )
    removals = [item for item in result["plan"] if item["action"] == "REMOVE_UNUSED_PLACEHOLDER"]
    assert {item["course_key"] for item in removals} == {
        row["course_key"] for row in placeholder_rows
    }
    assert removals[0]["before"]["created_at"] == placeholder_rows[0]["created_at"]
    assert result["placeholder_removal_count"] == 3
    assert result["extra_count"] == 0

    captured: list[dict] = []
    guarded_apply(
        result,
        expected_production_fingerprint=result["production_fingerprint"],
        expected_canonical_fingerprint=result["canonical_fingerprint"],
        actor="test-operator",
        reason="删除已核实未使用占位规则",
        reconciliation_batch_id="c3-placeholder-test-001",
        writer=lambda plan: captured.extend(plan),
    )
    assert any(item["action"] == "REMOVE_UNUSED_PLACEHOLDER" for item in captured)

    blocked = reconcile_course_rules(
        version={"id": 3, "status": "DRAFT"},
        production_rules=placeholder_rows,
        policy=policy,
    )
    blocked_items = [item for item in blocked["plan"] if item["action"] == "BLOCK"]
    assert len(blocked_items) == 3
    assert all("REFERENCE_EVIDENCE_MISSING" in item["reason"] for item in blocked_items)


def test_matching_requires_exact_class_and_does_not_use_group_as_identity() -> None:
    snapshot = {
        "tables": {
            "class_candidates": [
                {
                    "id": "class-1",
                    "name": "测试班",
                    "parent_id": "org-wujiang",
                    "unit_type": "CLASS",
                },
                {
                    "id": "class-old",
                    "name": "测试班",
                    "parent_id": "org-wujiang",
                    "unit_type": "CLASS",
                },
            ],
            "members": [
                {"id": 10, "name": "张三", "member_code": "M-10", "status": "ACTIVE"},
            ],
            "member_org_relations": [
                {
                    "member_id": 10,
                    "org_unit_id": "class-1",
                    "relation_type": "STUDY_CLASS",
                    "valid_from": None,
                    "valid_until": None,
                },
            ],
        }
    }
    rows = [
        {
            "source_sheet": "吴江",
            "source_row_number": 5,
            "raw_name": "张三",
            "raw_class_name": "测试班",
            "raw_group_name": "历史改名组",
            "validation_status": "PASS",
            "items": [{"source_month": 1}],
        },
    ]
    mappings = build_historical_class_mappings(snapshot=snapshot, source_rows=rows)
    matches = match_historical_rows(snapshot=snapshot, source_rows=rows, class_mappings=mappings)
    assert mappings[("吴江", "测试班")]["mapping_status"] == "AMBIGUOUS"
    assert matches[0]["identity_status"] == "AMBIGUOUS"
    assert matches[0]["matched_member_id"] is None


def test_period_report_marks_year_only_items_without_inventing_a_month() -> None:
    groups = summarize_period_items(
        [
            {
                "legacy_credit_type": "LEGACY_REPORT_EVENT",
                "source_sheet": "吴江",
                "source_column_name": "报告会",
                "points": 28,
                "source_month": None,
                "metadata": {
                    "period_track": "UNCLASSIFIED_PERIOD_REVIEW",
                    "period_resolution_status": "YEAR_ONLY_CONFIRMED",
                    "period_review_status": "PERIOD_REVIEW_REQUIRED",
                },
            }
        ]
    )
    assert groups == [
        {
            "legacy_credit_type": "LEGACY_REPORT_EVENT",
            "source_sheet": "吴江",
            "source_column_name": "报告会",
            "count": 1,
            "points_total": 28,
            "period_resolution_statuses": ["YEAR_ONLY_CONFIRMED"],
            "period_review_statuses": ["PERIOD_REVIEW_REQUIRED"],
        }
    ]


def test_migration_comments_pin_the_canonical_source_sha256() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    expected = "D4CFF89F03614D8300407BA67470EED6488DD68FF7FB6CCF060E2129CEF40886"
    for dialect in ("sqlite", "mysql"):
        sql = (
            repo_root / "migrations" / dialect / "0064_fix_credit_rule_mapping_and_binding_freeze.sql"
        ).read_text(encoding="utf-8")
        assert f"canonical_source_sha256: {expected}" in sql
