from __future__ import annotations

import hashlib
from io import BytesIO

from openpyxl import Workbook

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.historical_credit_import import (
    HISTORICAL_IMPORT_TYPE,
    HISTORICAL_SOURCE_RULE_VERSION,
    dry_run_historical_credit_import,
    get_historical_credit_import_row,
    list_historical_credit_import_anomalies,
    register_suzhou_credit_workbook,
)


def _small_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "测试班"
    sheet.append(["历史学分测试"])
    sheet.append(["序号", "姓名", "组别", "学习内容", "每日读书+读书优秀分享", None, "班级学习日"])
    sheet.append([None, None, None, "总分值", "1月", "2月", "1月（18分）"])
    sheet.append([None, None, None, None, 1, 0, 2])
    sheet.append([1, "测试学员一", "1组", "", "=1", 0, "=1+1"])
    sheet.append([2, "测试学员二", "1组", "", 3, 0, None])
    sheet.append([3, "测试学员三", "1组", "", 0, 0, 0])
    sheet.append([4, "测试学员四", "1组", "", None, None, None])
    # The first data row has a formula total, the next has a blank total, the
    # last two explicitly test zero/no-credit distinctions.
    sheet["D5"] = "=SUM(E5:G5)"
    sheet["D6"] = None
    sheet["D7"] = 0
    sheet["D8"] = None
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _cleanup(batch_id: int) -> None:
    with transaction() as connection:
        execute(connection, "DELETE FROM learning_credit_import_batches WHERE id=?", (batch_id,))


def test_c0_published_mapping_and_frozen_policy_are_present() -> None:
    version = fetch_one(
        "SELECT status, based_on_version_label FROM learning_plan_credit_rule_versions "
        "WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1'"
    )
    assert version == {"status": "PUBLISHED", "based_on_version_label": "2026"}
    assert fetch_one(
        "SELECT status, generic_rule_version_id, course_credit_rule_version_id "
        "FROM learning_plan_credit_rule_mappings "
        "WHERE plan_key='standard-3y' AND plan_version_label='2026'"
    )
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM learning_plan_credit_rules r "
        "JOIN learning_plan_credit_rule_versions v ON v.id=r.rule_version_id "
        "WHERE v.plan_key='STANDARD_3Y_2026' AND v.version_label='2026.1'"
    )["count"] == 25


def test_c1_workbook_import_is_idempotent_and_keeps_ledger_empty() -> None:
    content = _small_workbook()
    before = int(fetch_one("SELECT COUNT(*) AS count FROM learning_credit_entries")["count"])
    first = register_suzhou_credit_workbook(
        content=content, original_filename="fixture.xlsx", actor_user_id=None
    )
    try:
        assert first["idempotent"] is False
        batch = first["batch"]
        assert batch["status"] == "NEEDS_REVIEW"
        assert batch["metadata"]["source_rule_version"] == HISTORICAL_SOURCE_RULE_VERSION
        assert batch["validation_counts"] == {"PASS": 2, "TOTAL_MISSING": 1, "ZERO": 1}
        assert batch["item_count"] == 3
        assert batch["metadata"]["confirmed_source_total"] == 3
        assert batch["metadata"]["review_suggested_total"] == 3

        anomalies = list_historical_credit_import_anomalies(batch["id"])
        assert [item["validation_status"] for item in anomalies] == ["TOTAL_MISSING", "ZERO"]
        row = get_historical_credit_import_row(batch["id"], anomalies[0]["id"])
        assert row["match_status"] == "NOT_RUN"
        assert row["matched_member_id"] is None
        assert len(row["items"]) == 1

        preview = dry_run_historical_credit_import(batch["id"])
        assert preview["proposed_item_count"] == 0
        assert preview["blocked_reason"] == "PLATFORM_MEMBER_SNAPSHOT_REQUIRED"
        assert preview["learning_credit_entries_delta"] == 0
        assert int(fetch_one("SELECT COUNT(*) AS count FROM learning_credit_entries")["count"]) == before

        repeated = register_suzhou_credit_workbook(
            content=content, original_filename="fixture-renamed.xlsx", actor_user_id=None
        )
        assert repeated["idempotent"] is True
        assert repeated["batch"]["id"] == batch["id"]
    finally:
        _cleanup(int(first["batch"]["id"]))


def test_c1_import_identity_is_file_hash_and_import_type() -> None:
    content = _small_workbook()
    digest = hashlib.sha256(content).hexdigest()
    result = register_suzhou_credit_workbook(content=content, original_filename="fixture.xlsx")
    try:
        stored = fetch_one(
            "SELECT file_sha256, import_type, source_rule_version FROM learning_credit_import_batches WHERE id=?",
            (result["batch"]["id"],),
        )
        assert stored == {
            "file_sha256": digest,
            "import_type": HISTORICAL_IMPORT_TYPE,
            "source_rule_version": HISTORICAL_SOURCE_RULE_VERSION,
        }
        assert fetch_all(
            "SELECT match_status, matched_member_id FROM learning_credit_import_rows WHERE batch_id=?",
            (result["batch"]["id"],),
        )
        assert all(row["match_status"] == "NOT_RUN" and row["matched_member_id"] is None for row in fetch_all(
            "SELECT match_status, matched_member_id FROM learning_credit_import_rows WHERE batch_id=?",
            (result["batch"]["id"],),
        ))
    finally:
        _cleanup(int(result["batch"]["id"]))


def test_c1_parser_contract_uses_historical_rule_identity_without_current_recalculation() -> None:
    content = _small_workbook()
    result = register_suzhou_credit_workbook(content=content, original_filename="fixture.xlsx")
    try:
        item = fetch_one(
            "SELECT source_rule_version, legacy_credit_type, credit_category, source_row_number, points, status "
            "FROM learning_credit_import_items WHERE batch_id=? ORDER BY id LIMIT 1",
            (result["batch"]["id"],),
        )
        assert item == {
            "source_rule_version": HISTORICAL_SOURCE_RULE_VERSION,
            "legacy_credit_type": "LEGACY_READING_AND_SHARE",
            "credit_category": "STANDARD_LEARNING",
            "source_row_number": 5,
            "points": 1.0,
            "status": "PENDING_REVIEW",
        }
        assert fetch_one(
            "SELECT COUNT(*) AS count FROM learning_credit_entries WHERE source_type='HISTORICAL_CREDIT_IMPORT'"
        )["count"] == 0
    finally:
        _cleanup(int(result["batch"]["id"]))
