from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app import db
from app.core.settings import get_settings
from app.db import execute, transaction
from app.migrations import run_migrations
from app.services import production_operations
from app.services.course_credit_canonical import canonical_fingerprint, load_canonical_policy
from app.services.course_credit_reconciliation import (
    UNUSED_PLACEHOLDER_KEYS,
    production_fingerprint,
)
from app.services.iam import seed_iam
from app.services.production_operations import (
    ProductionOperationError,
    apply_g5_4_course_rule_reconciliation,
)


RELEASE_COMMIT = "c" * 40
KEEP_KEYS = {
    "Y1-ACCOUNTING-ANALYSIS-TASK",
    "Y1-CLASS-SPEECH-DRAFT",
    "Y1-HAPPINESS-ASSESSMENT",
    "Y1-INTEGRATED-ACCOUNTING",
    "Y1-SEVEN-ACCOUNTING-PRINCIPLES",
    "Y1-SIX-DILIGENCES",
    "Y1-TWELVE-MANAGEMENT",
}
UPDATE_KEYS = {
    "AUTO-QR-AMOEBA-INTRODUCTION",
    "AUTO-QR-HUNDRED-DAY-CAMPAIGN",
    "Y1-ANNUAL-MONTHLY-MGMT",
    "Y1-KYOCERA-ANNUAL-PLAN",
}


@pytest.fixture(scope="module", autouse=True)
def isolated_apply_database(tmp_path_factory: pytest.TempPathFactory):
    patcher = pytest.MonkeyPatch()
    original_settings = db.settings
    mysql_job = os.getenv("G5_4_PRODUCTION_APPLY_MYSQL") == "1"
    if not mysql_job:
        database = tmp_path_factory.mktemp("g54-apply") / "apply.db"
        patcher.setenv("DATABASE_URL", f"sqlite:///{database.as_posix()}")
    patcher.setenv("APP_ENV", "test")
    patcher.setenv("DEPLOYMENT_READ_ONLY", "false")
    patcher.setenv("ALLOW_PRODUCTION_MUTATIONS", "true")
    patcher.setenv("G5_4_PRODUCTION_RULE_APPLY_ENABLED", "true")
    patcher.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")
    patcher.setenv("RUN_BOOTSTRAP_ON_STARTUP", "false")
    db.settings = get_settings()
    if not mysql_job:
        run_migrations()
        seed_iam()
    patcher.setattr(
        production_operations,
        "get_build_info",
        lambda: {"commit_sha": RELEASE_COMMIT},
    )
    try:
        yield
    finally:
        db.settings = original_settings
        patcher.undo()


def _fetch_one(connection, statement: str, params: tuple = ()) -> dict | None:
    row = execute(connection, statement, params).fetchone()
    return dict(row) if row else None


def _ensure_org(connection, org_id: str, code: str, name: str, unit_type: str, parent_id=None) -> None:
    if _fetch_one(connection, "SELECT id FROM org_units WHERE id=?", (org_id,)):
        return
    now = datetime.now(UTC).replace(tzinfo=None)
    execute(
        connection,
        "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
        (org_id, code, name, unit_type, parent_id, now, now),
    )


def _prepare_before_state() -> dict:
    policy = load_canonical_policy()
    canonical = {row["course_key"]: row for row in policy["rules"]}
    with transaction() as connection:
        execute(connection, "DELETE FROM study_meeting_course_completions")
        execute(connection, "DELETE FROM study_meeting_courses")
        execute(connection, "DELETE FROM study_meeting_sessions WHERE session_code LIKE 'G54-C32-%'")
        execute(connection, "DELETE FROM learning_credit_entries")
        execute(
            connection,
            "DELETE FROM audit_logs WHERE action LIKE 'production.g5_4.course_rule_reconciliation.%'",
        )
        execute(
            connection,
            "DELETE FROM schema_migrations WHERE version IN (?, ?, ?)",
            (
                "0064_fix_credit_rule_mapping_and_binding_freeze.sql",
                "0065_learning_credit_history_import.sql",
                "0066_historical_credit_time_precision_review.sql",
            ),
        )
        execute(
            connection,
            "UPDATE class_learning_bindings SET credit_rule_version_id=NULL, "
            "course_credit_rule_version_id=NULL",
        )
        execute(connection, "DELETE FROM learning_plan_credit_rule_mappings")

        version = _fetch_one(
            connection,
            "SELECT id FROM learning_plan_credit_rule_versions "
            "WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1'",
        )
        assert version
        version_id = int(version["id"])
        execute(connection, "DELETE FROM learning_plan_credit_rules WHERE rule_version_id=?", (version_id,))
        execute(
            connection,
            "UPDATE learning_plan_credit_rule_versions SET status='DRAFT', based_on_version_label='2026' "
            "WHERE id=?",
            (version_id,),
        )

        rows: list[dict] = []
        for key in sorted(KEEP_KEYS | UPDATE_KEYS):
            row = {**canonical[key], "status": "CONFIGURED", "source": "BASELINE"}
            if key == "AUTO-QR-AMOEBA-INTRODUCTION":
                row.update(credit_points=0, status="PENDING", source="SYSTEM_DEFAULT")
            elif key == "AUTO-QR-HUNDRED-DAY-CAMPAIGN":
                row.update(
                    course_name="百日奋战学习",
                    credit_points=0,
                    status="PENDING",
                    source="SYSTEM_DEFAULT",
                    aliases=["百日奋战"],
                )
            elif key == "Y1-ANNUAL-MONTHLY-MGMT":
                row["credit_points"] = 15
            elif key == "Y1-KYOCERA-ANNUAL-PLAN":
                row["credit_points"] = 30
            rows.append(row)
        rows.extend(
            [
                {
                    "course_key": "AUTO-QR-EXCELLENT-IMPROVEMENT",
                    "course_name": "优秀改善创新案例分享",
                    "year_index": 2,
                    "credit_points": 0,
                    "status": "PENDING",
                    "source": "SYSTEM_DEFAULT",
                    "aliases": ["优秀改善创新案例分享"],
                },
                {
                    "course_key": "AUTO-QR-HAPPINESS-CARE",
                    "course_name": "幸福关爱委讲解",
                    "year_index": 2,
                    "credit_points": 0,
                    "status": "PENDING",
                    "source": "SYSTEM_DEFAULT",
                    "aliases": ["幸福关爱委"],
                },
                {
                    "course_key": "AUTO-QR-IMPROVEMENT-INNOVATION",
                    "course_name": "改善创新委讲解与案例分享",
                    "year_index": 2,
                    "credit_points": 0,
                    "status": "PENDING",
                    "source": "SYSTEM_DEFAULT",
                    "aliases": ["改善创新委", "改善创新案例"],
                },
            ]
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        for row in rows:
            execute(
                connection,
                "INSERT INTO learning_plan_credit_rules "
                "(rule_version_id, course_key, course_name, year_index, credit_points, status, "
                "source, aliases_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version_id,
                    row["course_key"],
                    row["course_name"],
                    row["year_index"],
                    row["credit_points"],
                    row["status"],
                    row["source"],
                    json.dumps(row["aliases"], ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                ),
            )

        plan = _fetch_one(
            connection,
            "SELECT id FROM learning_plan_versions WHERE plan_key='standard-3y' AND version_label='2026'",
        )
        if not plan:
            cursor = execute(
                connection,
                "INSERT INTO learning_plan_versions "
                "(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) "
                "VALUES ('standard-3y', '测试三年计划', '2026', 36, 'PUBLISHED', ?, ?)",
                (now, now),
            )
            plan_id = int(cursor.lastrowid)
        else:
            plan_id = int(plan["id"])
        existing = _fetch_one(
            connection,
            "SELECT COUNT(*) AS n FROM class_learning_bindings WHERE plan_version_id=? AND status='ACTIVE'",
            (plan_id,),
        )
        for index in range(int(existing["n"]), 19):
            org_id = f"g54-c32-class-{index + 1:02d}"
            _ensure_org(connection, org_id, f"G54C32{index + 1:02d}", f"C3.2测试班{index + 1}", "CLASS")
            execute(
                connection,
                "INSERT INTO class_learning_bindings "
                "(class_org_unit_id, plan_version_id, started_at, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'ACTIVE', ?, ?)",
                (org_id, plan_id, now, now, now),
            )
        actor = _fetch_one(
            connection,
            "SELECT u.id FROM app_users u JOIN user_roles ur ON ur.user_id=u.id "
            "WHERE ur.role_key='system_admin' AND u.is_active=1 ORDER BY u.id LIMIT 1",
        )
        assert actor
        stored_rows = [
            dict(row)
            for row in execute(
                connection,
                "SELECT id, course_key, course_name, year_index, credit_points, status, source, "
                "aliases_json, created_by, updated_by, created_at, updated_at "
                "FROM learning_plan_credit_rules WHERE rule_version_id=? ORDER BY course_key",
                (version_id,),
            ).fetchall()
        ]
        version_row = _fetch_one(
            connection,
            "SELECT id, plan_key, version_label, status, based_on_version_label "
            "FROM learning_plan_credit_rule_versions WHERE id=?",
            (version_id,),
        )
    return {
        "version_id": version_id,
        "actor_user_id": int(actor["id"]),
        "production_fingerprint": production_fingerprint(version_row, stored_rows),
        "canonical_fingerprint": canonical_fingerprint(policy),
    }


@pytest.fixture(autouse=True)
def approved_before_state() -> dict:
    return _prepare_before_state()


def _request(state: dict, **overrides) -> dict:
    values = {
        "expected_release_commit": RELEASE_COMMIT,
        "expected_production_fingerprint": state["production_fingerprint"],
        "expected_canonical_fingerprint": state["canonical_fingerprint"],
        "expected_course_rule_version_id": state["version_id"],
        "expected_course_rule_status": "DRAFT",
        "expected_rule_count": 14,
        "expected_placeholder_keys": sorted(UNUSED_PLACEHOLDER_KEYS),
        "execution_reason": "G5.4-C3.2 isolated transaction verification",
        "actor_user_id": state["actor_user_id"],
    }
    values.update(overrides)
    return values


def _state_counts() -> tuple[int, int, int]:
    with transaction() as connection:
        rules = _fetch_one(connection, "SELECT COUNT(*) AS n FROM learning_plan_credit_rules")
        placeholders = _fetch_one(
            connection,
            "SELECT COUNT(*) AS n FROM learning_plan_credit_rules WHERE course_key IN (?, ?, ?)",
            tuple(sorted(UNUSED_PLACEHOLDER_KEYS)),
        )
        audits = _fetch_one(
            connection,
            "SELECT COUNT(*) AS n FROM audit_logs "
            "WHERE action LIKE 'production.g5_4.course_rule_reconciliation.%'",
        )
    return int(rules["n"]), int(placeholders["n"]), int(audits["n"])


def test_successful_atomic_apply_and_idempotent_repeat(approved_before_state: dict) -> None:
    applied = apply_g5_4_course_rule_reconciliation(**_request(approved_before_state))
    assert applied["status"] == "APPLIED"
    assert applied["changes"] == {"keep": 7, "update": 4, "add": 14, "remove": 3}
    assert applied["before"]["rule_count"] == 14
    assert applied["after"] == {"rule_count": 25, "fingerprint": approved_before_state["canonical_fingerprint"]}
    assert applied["ledger_delta"] == 0
    with transaction() as connection:
        total_audit = _fetch_one(
            connection,
            "SELECT before_json, after_json FROM audit_logs "
            "WHERE action='production.g5_4.course_rule_reconciliation.apply'",
        )
        delete_audit = _fetch_one(
            connection,
            "SELECT before_json FROM audit_logs "
            "WHERE action='production.g5_4.course_rule_reconciliation.remove_unused_placeholder' "
            "ORDER BY id LIMIT 1",
        )
    total_after = json.loads(total_audit["after_json"])
    assert total_after["production_fingerprint_after"] == approved_before_state["canonical_fingerprint"]
    assert total_after["release_commit"] == RELEASE_COMMIT
    deleted_before = json.loads(delete_audit["before_json"])
    assert {"id", "course_key", "aliases_json", "created_by", "updated_by", "created_at", "updated_at"} <= set(deleted_before)
    audit_count = _state_counts()[2]
    repeated = apply_g5_4_course_rule_reconciliation(**_request(approved_before_state))
    assert repeated["status"] == "ALREADY_APPLIED"
    assert repeated["changes"] == {"keep": 25, "update": 0, "add": 0, "remove": 0}
    assert _state_counts()[2] == audit_count


@pytest.mark.parametrize(
    ("action", "ordinal"),
    [("UPDATE", 2), ("REMOVE_UNUSED_PLACEHOLDER", 1), ("ADD", 7)],
)
def test_failure_injection_rolls_back_everything(
    approved_before_state: dict, action: str, ordinal: int
) -> None:
    def fail(current_action: str, current_ordinal: int) -> None:
        if (current_action, current_ordinal) == (action, ordinal):
            raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected failure"):
        apply_g5_4_course_rule_reconciliation(
            **_request(approved_before_state),
            fault_injector=fail,
        )
    assert _state_counts() == (14, 3, 0)


def test_production_fingerprint_drift_is_zero_write(approved_before_state: dict) -> None:
    with transaction() as connection:
        execute(
            connection,
            "UPDATE learning_plan_credit_rules SET credit_points=credit_points+1 "
            "WHERE course_key='Y1-HAPPINESS-ASSESSMENT'",
        )
    with pytest.raises(ProductionOperationError) as caught:
        apply_g5_4_course_rule_reconciliation(**_request(approved_before_state))
    assert caught.value.code == "PRODUCTION_FINGERPRINT_MISMATCH"
    assert _state_counts()[2] == 0


def test_canonical_fingerprint_drift_is_zero_write(approved_before_state: dict) -> None:
    with pytest.raises(ProductionOperationError) as caught:
        apply_g5_4_course_rule_reconciliation(
            **_request(approved_before_state, expected_canonical_fingerprint="0" * 64)
        )
    assert caught.value.code == "CANONICAL_FINGERPRINT_MISMATCH"
    assert _state_counts() == (14, 3, 0)


def test_release_commit_mismatch_is_zero_write(approved_before_state: dict) -> None:
    with pytest.raises(ProductionOperationError) as caught:
        apply_g5_4_course_rule_reconciliation(
            **_request(approved_before_state, expected_release_commit="d" * 40)
        )
    assert caught.value.code == "RELEASE_COMMIT_MISMATCH"
    assert _state_counts() == (14, 3, 0)


def test_settlement_flag_true_is_zero_write(approved_before_state: dict, monkeypatch) -> None:
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
    with pytest.raises(ProductionOperationError) as caught:
        apply_g5_4_course_rule_reconciliation(**_request(approved_before_state))
    assert caught.value.code == "SETTLEMENT_ENABLED"
    assert _state_counts() == (14, 3, 0)


def test_ledger_nonzero_blocks_before_rule_write(approved_before_state: dict) -> None:
    with transaction() as connection:
        now = datetime.now(UTC).replace(tzinfo=None)
        member = _fetch_one(connection, "SELECT id FROM members WHERE member_code='G54-C32-MEMBER'")
        if not member:
            cursor = execute(
                connection,
                "INSERT INTO members(member_code, name, org_unit_id, status, sensitivity_level, created_at, updated_at) "
                "VALUES ('G54-C32-MEMBER', 'C3.2测试成员', 'g54-c32-class-01', 'ACTIVE', 'INTERNAL', ?, ?)",
                (now, now),
            )
            member_id = int(cursor.lastrowid)
        else:
            member_id = int(member["id"])
        generic = _fetch_one(
            connection,
            "SELECT id FROM learning_credit_rule_versions "
            "WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1'",
        )
        execute(
            connection,
            "INSERT INTO learning_credit_entries "
            "(member_id, credit_category, credit_type, points, source_type, source_id, rule_key, "
            "rule_version, rule_version_id, rule_snapshot_json, occurred_at, occurred_precision, "
            "occurred_year, occurred_month, status, idempotency_key, created_at, updated_at) "
            "VALUES (?, 'STANDARD_LEARNING', 'DAILY_READING', 1, 'G54_C32_TEST', '1', "
            "'DAILY_READING', '2026.1', ?, '{}', ?, 'EXACT_DATE', 2026, 9, 'PENDING', "
            "'g54-c32-ledger-nonzero', ?, ?)",
            (member_id, generic["id"], now, now, now),
        )
    with pytest.raises(ProductionOperationError) as caught:
        apply_g5_4_course_rule_reconciliation(**_request(approved_before_state))
    assert caught.value.code == "LEDGER_NOT_EMPTY"
    assert _state_counts()[2] == 0


def test_placeholder_reference_blocks_before_delete(approved_before_state: dict) -> None:
    with transaction() as connection:
        generic = _fetch_one(
            connection,
            "SELECT id FROM learning_credit_rule_versions "
            "WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1'",
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        execute(
            connection,
            "INSERT INTO learning_plan_credit_rule_mappings "
            "(plan_key, plan_version_label, generic_rule_version_id, course_credit_rule_version_id, "
            "status, mapping_source, created_at, updated_at) "
            "VALUES ('standard-3y', '2026', ?, ?, 'ACTIVE', 'G54-C32-TEST', ?, ?)",
            (generic["id"], approved_before_state["version_id"], now, now),
        )
    with pytest.raises(ProductionOperationError) as caught:
        apply_g5_4_course_rule_reconciliation(**_request(approved_before_state))
    assert caught.value.code == "PLACEHOLDER_REFERENCE_FOUND"
    assert _state_counts() == (14, 3, 0)


def test_unknown_intermediate_state_is_rejected(approved_before_state: dict) -> None:
    with transaction() as connection:
        version = _fetch_one(
            connection,
            "SELECT id FROM learning_plan_credit_rule_versions "
            "WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1'",
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        for index in range(6):
            execute(
                connection,
                "INSERT INTO learning_plan_credit_rules "
                "(rule_version_id, course_key, course_name, credit_points, status, source, aliases_json, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, 'CONFIGURED', 'BASELINE', '[]', ?, ?)",
                (version["id"], f"INTERMEDIATE-{index}", f"中间态{index}", now, now),
            )
    with pytest.raises(ProductionOperationError) as caught:
        apply_g5_4_course_rule_reconciliation(**_request(approved_before_state))
    assert caught.value.code == "UNKNOWN_INTERMEDIATE_STATE"
    assert _state_counts()[2] == 0


def test_concurrent_apply_is_blocked_by_fixed_operation_lock(approved_before_state: dict) -> None:
    entered = threading.Event()
    release = threading.Event()
    first_result: dict = {}

    def hold_after_first_update(action: str, ordinal: int) -> None:
        if action == "UPDATE" and ordinal == 1:
            entered.set()
            assert release.wait(15)

    def first_apply() -> None:
        first_result.update(
            apply_g5_4_course_rule_reconciliation(
                **_request(approved_before_state),
                fault_injector=hold_after_first_update,
            )
        )

    worker = threading.Thread(target=first_apply, daemon=True)
    worker.start()
    assert entered.wait(15)
    try:
        with pytest.raises(ProductionOperationError) as caught:
            apply_g5_4_course_rule_reconciliation(**_request(approved_before_state))
        assert caught.value.code == "APPLY_ALREADY_RUNNING"
    finally:
        release.set()
        worker.join(20)
    assert not worker.is_alive()
    assert first_result["status"] == "APPLIED"
