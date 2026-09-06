from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.db import connect, execute, fetch_one, transaction
from app.services.credit_rule_mapping import resolve_credit_rule_mapping
from app.services.learning_credits import post_credit_entry
from app.services.learning_cycles import correct_class_learning_plan, restart_class_learning_plan
from test_v12_mvp import _seed_group_leader_fixture


@pytest.fixture(autouse=True)
def g5_3_1_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _admin_id() -> int:
    return int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])


def _published_policy_ids() -> dict[str, int | str]:
    row = fetch_one(
        "SELECT generic_rule_version_id, course_credit_rule_version_id "
        "FROM learning_plan_credit_rule_mappings "
        "WHERE plan_key='standard-3y' AND plan_version_label='2026' "
        "AND status='ACTIVE'"
    )
    assert row
    generic = fetch_one(
        "SELECT id, rule_set_key, version_label FROM learning_credit_rule_versions WHERE id=?",
        (row["generic_rule_version_id"],),
    )
    course = fetch_one(
        "SELECT id, plan_key, version_label FROM learning_plan_credit_rule_versions WHERE id=?",
        (row["course_credit_rule_version_id"],),
    )
    assert generic and course
    return {
        "generic_id": int(generic["id"]),
        "generic_rule_set_key": generic["rule_set_key"],
        "generic_version_label": generic["version_label"],
        "course_id": int(course["id"]),
        "course_rule_plan_key": course["plan_key"],
        "course_version_label": course["version_label"],
    }


def _create_plan(*, mapped: bool) -> int:
    suffix = uuid4().hex[:10]
    plan_key = f"G531_{'MAPPED' if mapped else 'UNMAPPED'}_{suffix}"
    version_label = "2026"
    now = _now()
    policy = _published_policy_ids() if mapped else None
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO learning_plan_versions "
            "(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 3, 'PUBLISHED', ?, ?)",
            (plan_key, f"G5.3.1修正测试计划-{suffix}", version_label, now, now),
        )
        plan_id = int(cursor.lastrowid)
        for cycle_index in (1, 2, 3):
            cycle_cursor = execute(
                connection,
                "INSERT INTO learning_plan_cycles "
                "(plan_version_id, cohort_month, cycle_index, year_index, cycle_label, created_at, updated_at) "
                "VALUES (?, 4, ?, 1, ?, ?, ?)",
                (plan_id, cycle_index, f"G5.3.1第{cycle_index}周期", now, now),
            )
            execute(
                connection,
                "INSERT INTO learning_plan_tasks "
                "(plan_cycle_id, task_type, title, is_required, sort_order, created_at, updated_at) "
                "VALUES (?, 'GROUP_MEETING', ?, 1, 1, ?, ?)",
                (int(cycle_cursor.lastrowid), f"G5.3.1第{cycle_index}期小组会", now, now),
            )
        if policy:
            execute(
                connection,
                "INSERT INTO learning_plan_credit_rule_mappings "
                "(plan_key, plan_version_label, generic_rule_version_id, "
                "course_credit_rule_version_id, status, mapping_source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'ACTIVE', 'G5.3.1_TEST', ?, ?)",
                (plan_key, version_label, policy["generic_id"], policy["course_id"], now, now),
            )
    return plan_id


def _start_wrong_round(fixture: dict, admin_id: int) -> dict:
    wrong_plan_id = _create_plan(mapped=False)
    result = restart_class_learning_plan(
        actor_user_id=admin_id,
        class_org_unit_id=fixture["class_id"],
        plan_version_id=wrong_plan_id,
        cohort_month=4,
        started_at=_now(),
        reason="G5.3.1 建立待修正的错误计划绑定",
    )
    binding = fetch_one(
        "SELECT b.id, b.plan_version_id, b.learning_round, b.cohort_month, "
        "b.credit_rule_version_id, b.course_credit_rule_version_id "
        "FROM class_learning_bindings b WHERE b.id=?",
        (result["binding"]["id"],),
    )
    assert binding
    return dict(binding)


def _mapping_for_plan(plan_id: int) -> dict:
    plan = fetch_one(
        "SELECT plan_key, version_label FROM learning_plan_versions WHERE id=?",
        (plan_id,),
    )
    assert plan
    connection = connect()
    try:
        mapping = resolve_credit_rule_mapping(
            connection,
            plan_key=plan["plan_key"],
            version_label=plan["version_label"],
        )
    finally:
        connection.close()
    assert mapping
    return mapping


def test_cross_plan_correction_keeps_binding_round_and_updates_frozen_mapping() -> None:
    fixture = _seed_group_leader_fixture()
    admin_id = _admin_id()
    binding_before = _start_wrong_round(fixture, admin_id)
    target_plan_id = _create_plan(mapped=True)
    target_plan = fetch_one(
        "SELECT plan_key, version_label FROM learning_plan_versions WHERE id=?",
        (target_plan_id,),
    )
    assert target_plan
    target_mapping = _mapping_for_plan(target_plan_id)
    binding_count_before = int(
        fetch_one(
            "SELECT COUNT(*) AS n FROM class_learning_bindings WHERE class_org_unit_id=?",
            (fixture["class_id"],),
        )["n"]
    )
    current_cycle_before = fetch_one(
        "SELECT id, learning_cycle_index, plan_cycle_id FROM class_learning_cycles WHERE binding_id=?",
        (binding_before["id"],),
    )
    assert current_cycle_before

    corrected = correct_class_learning_plan(
        actor_user_id=admin_id,
        class_org_unit_id=fixture["class_id"],
        plan_version_id=target_plan_id,
        cohort_month=4,
        learning_cycle_index=1,
        reason="修正历史误绑的学习计划版本",
    )

    assert corrected["binding"]["id"] == binding_before["id"]
    assert corrected["binding"]["learning_round"] == binding_before["learning_round"]
    assert corrected["binding"]["transition_type"] == "CORRECTION"
    assert corrected["binding"]["plan_version_id"] == target_plan_id
    assert corrected["binding"]["cohort_month"] == 4
    assert corrected["binding"]["credit_rule_version_id"] == target_mapping[
        "generic_rule_version_id"
    ]
    assert corrected["binding"]["course_credit_rule_version_id"] == target_mapping[
        "course_credit_rule_version_id"
    ]
    assert corrected["current_cycle"]["id"] == current_cycle_before["id"]
    assert corrected["current_cycle"]["learning_cycle_index"] == 1
    assert corrected["current_cycle"]["plan_cycle_id"] != current_cycle_before["plan_cycle_id"]
    assert int(
        fetch_one(
            "SELECT COUNT(*) AS n FROM class_learning_bindings WHERE class_org_unit_id=?",
            (fixture["class_id"],),
        )["n"]
    ) == binding_count_before

    audit = fetch_one(
        "SELECT before_json, after_json FROM audit_logs "
        "WHERE action='learning.binding.correction' AND resource_id=? "
        "ORDER BY id DESC LIMIT 1",
        (str(binding_before["id"]),),
    )
    assert audit
    before = json.loads(audit["before_json"])
    after = json.loads(audit["after_json"])
    assert before["correction_snapshot"]["plan_version"]["version_label"] == "2026"
    assert before["correction_snapshot"]["learning_round"] == binding_before["learning_round"]
    assert before["correction_snapshot"]["current_cycle"]["id"] == current_cycle_before["id"]
    assert after["correction_snapshot"]["plan_version"]["id"] == target_plan_id
    assert after["correction_snapshot"]["generic_credit_rule_version"]["id"] == target_mapping[
        "generic_rule_version_id"
    ]
    assert after["correction_snapshot"]["course_credit_rule_version"]["id"] == target_mapping[
        "course_credit_rule_version_id"
    ]


def test_cross_plan_correction_rejects_target_without_explicit_mapping() -> None:
    fixture = _seed_group_leader_fixture()
    admin_id = _admin_id()
    binding_before = _start_wrong_round(fixture, admin_id)
    target_plan_id = _create_plan(mapped=False)
    with pytest.raises(ValueError, match="RULE_MAPPING_MISSING"):
        correct_class_learning_plan(
            actor_user_id=admin_id,
            class_org_unit_id=fixture["class_id"],
            plan_version_id=target_plan_id,
            cohort_month=4,
            learning_cycle_index=1,
            reason="目标计划未配置显式学分映射",
        )
    binding_after = fetch_one(
        "SELECT plan_version_id, learning_round, cohort_month, "
        "credit_rule_version_id, course_credit_rule_version_id "
        "FROM class_learning_bindings WHERE id=?",
        (binding_before["id"],),
    )
    assert binding_after == {
        "plan_version_id": binding_before["plan_version_id"],
        "learning_round": binding_before["learning_round"],
        "cohort_month": binding_before["cohort_month"],
        "credit_rule_version_id": binding_before["credit_rule_version_id"],
        "course_credit_rule_version_id": binding_before["course_credit_rule_version_id"],
    }


def test_cross_plan_correction_with_formal_entry_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _seed_group_leader_fixture()
    admin_id = _admin_id()
    binding_before = _start_wrong_round(fixture, admin_id)
    target_plan_id = _create_plan(mapped=True)
    policy = _published_policy_ids()
    cycle = fetch_one(
        "SELECT id FROM class_learning_cycles WHERE binding_id=? AND cycle_status='OPEN'",
        (binding_before["id"],),
    )
    assert cycle
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
    post_credit_entry(
        actor_user_id=admin_id,
        item={
            "member_id": fixture["member_id"],
            "credit_category": "STANDARD_LEARNING",
            "credit_type": "GROUP_MEETING_ATTENDANCE",
            "points": 4,
            "source_type": "G5_3_1_TEST",
            "source_id": uuid4().hex,
            "class_org_unit_id": fixture["class_id"],
            "learning_cycle_id": cycle["id"],
            "rule_key": "GROUP_MEETING_ATTENDANCE",
            "rule_version": policy["generic_version_label"],
            "rule_version_id": policy["generic_id"],
            "rule_snapshot": {"source": "G5.3.1 test"},
            "occurred_at": "2026-09-07",
            "idempotency_key": f"G5.3.1:{uuid4().hex}",
        },
    )
    with pytest.raises(ValueError, match="已有正式学分记录"):
        correct_class_learning_plan(
            actor_user_id=admin_id,
            class_org_unit_id=fixture["class_id"],
            plan_version_id=target_plan_id,
            cohort_month=4,
            learning_cycle_index=1,
            reason="正式账本后禁止跨计划直接修正",
        )
    current = fetch_one(
        "SELECT plan_version_id, learning_round FROM class_learning_bindings WHERE id=?",
        (binding_before["id"],),
    )
    assert current == {
        "plan_version_id": binding_before["plan_version_id"],
        "learning_round": binding_before["learning_round"],
    }
