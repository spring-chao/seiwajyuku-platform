from __future__ import annotations

from io import BytesIO
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from openpyxl import Workbook

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.historical_credit_import import (
    dry_run_historical_credit_import,
    register_suzhou_credit_workbook,
)
from app.services.historical_credit_review import (
    accept_year_only_period,
    approve_calculated_totals,
    bulk_confirm_high_confidence_matches,
    confirm_no_credit,
    get_historical_credit_review_workbench,
    upsert_class_mapping_candidates,
)
from app.services.learning_credits import _insert_entry


def _admin_id() -> int:
    return int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])


def _ensure_member() -> tuple[int, bool]:
    existing = fetch_one("SELECT id FROM members ORDER BY id LIMIT 1")
    if existing:
        return int(existing["id"]), False
    org = fetch_one("SELECT id FROM org_units ORDER BY id LIMIT 1")
    assert org
    now = datetime.now(UTC).isoformat()
    code = f"C2C-{uuid4().hex[:12]}"
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO members (member_code, name, org_unit_id, status, sensitivity_level, created_at, updated_at) "
            "VALUES (?, ?, ?, 'ACTIVE', 'INTERNAL', ?, ?)",
            (code, "C2C测试学员", org["id"], now, now),
        )
        return int(cursor.lastrowid), True


def _cleanup(batch_id: int) -> None:
    with transaction() as connection:
        execute(connection, "DELETE FROM learning_credit_import_batches WHERE id=?", (batch_id,))


def _year_only_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "测试班"
    sheet.append(["历史学分测试"])
    sheet.append(["序号", "姓名", "组别", "学习内容", "每日读书", "班级学习日"])
    sheet.append([None, None, None, "总分值", "1月", "班级学习日"])
    sheet.append([None, None, None, None, 1, 4])
    sheet.append([1, "测试学员一", "1组", 5, 1, 4])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_ledger_period_precision_preserves_exact_month_and_year_without_fake_date() -> None:
    member_id, created_member = _ensure_member()
    keys = [f"C2C:{uuid4().hex}:{suffix}" for suffix in ("exact", "month", "year")]
    with transaction() as connection:
        exact = _insert_entry(
            connection,
            {
                "member_id": member_id, "credit_category": "STANDARD_LEARNING",
                "credit_type": "MANUAL_ADJUSTMENT", "points": 1,
                "source_type": "TEST", "source_id": keys[0], "rule_key": "MANUAL_ADJUSTMENT",
                "rule_version": "test", "rule_snapshot": {}, "occurred_at": "2026-09-18",
                "idempotency_key": keys[0],
            },
            status="PENDING", actor_user_id=None,
        )
        month = _insert_entry(
            connection,
            {
                "member_id": member_id, "credit_category": "STANDARD_LEARNING",
                "credit_type": "MANUAL_ADJUSTMENT", "points": 2,
                "source_type": "TEST", "source_id": keys[1], "rule_key": "MANUAL_ADJUSTMENT",
                "rule_version": "test", "rule_snapshot": {}, "occurred_precision": "MONTH",
                "occurred_year": 2026, "occurred_month": 8, "idempotency_key": keys[1],
            },
            status="PENDING", actor_user_id=None,
        )
        year = _insert_entry(
            connection,
            {
                "member_id": member_id, "credit_category": "STANDARD_LEARNING",
                "credit_type": "MANUAL_ADJUSTMENT", "points": 3,
                "source_type": "TEST", "source_id": keys[2], "rule_key": "MANUAL_ADJUSTMENT",
                "rule_version": "test", "rule_snapshot": {}, "occurred_precision": "YEAR",
                "occurred_year": 2026, "idempotency_key": keys[2],
            },
            status="PENDING", actor_user_id=None,
        )
        rows = [dict(row) for row in execute(
            connection,
            "SELECT occurred_at, occurred_precision, occurred_year, occurred_month "
            "FROM learning_credit_entries WHERE id IN (?, ?, ?) ORDER BY id",
            (exact["id"], month["id"], year["id"]),
        ).fetchall()]
        assert rows == [
            {"occurred_at": "2026-09-18", "occurred_precision": "EXACT_DATE", "occurred_year": 2026, "occurred_month": 9},
            {"occurred_at": None, "occurred_precision": "MONTH", "occurred_year": 2026, "occurred_month": 8},
            {"occurred_at": None, "occurred_precision": "YEAR", "occurred_year": 2026, "occurred_month": None},
        ]
        execute(connection, "DELETE FROM learning_credit_entries WHERE id IN (?, ?, ?)", (exact["id"], month["id"], year["id"]))
        if created_member:
            execute(connection, "DELETE FROM members WHERE id=?", (member_id,))


def test_year_only_acceptance_is_audited_and_unblocks_only_the_year_item() -> None:
    result = register_suzhou_credit_workbook(content=_year_only_workbook(), original_filename="year-only.xlsx")
    batch_id = int(result["batch"]["id"])
    try:
        member_id, created_member = _ensure_member()
        with transaction() as connection:
            execute(
                connection,
                "UPDATE learning_credit_import_rows SET matched_member_id=?, match_status='AUTO_MATCHED' WHERE batch_id=?",
                (member_id, batch_id),
            )
            execute(
                connection,
                "UPDATE learning_credit_import_items SET matched_member_id=? WHERE batch_id=?",
                (member_id, batch_id),
            )
        before = int(fetch_one("SELECT COUNT(*) AS count FROM learning_credit_entries")["count"])
        pending = dry_run_historical_credit_import(batch_id)
        assert pending["proposed_item_count"] == 1
        assert pending["blocked_reason_counts"]["PERIOD_YEAR_ACCEPTANCE_REQUIRED"]["count"] == 1
        accepted = accept_year_only_period(
            batch_id=batch_id, expected_count=1, actor_user_id=_admin_id(),
            reason="来源文件只证明2026年度，接受年度精度，不推定月份",
        )
        assert accepted["accepted_count"] == 1
        after = dry_run_historical_credit_import(batch_id)
        assert after["proposed_item_count"] == 2
        assert after["proposed_points"] == 5
        assert after["track_counts"]["YEAR_ACCEPTED"]["count"] == 1
        assert after["learning_credit_entries_delta"] == 0
        assert fetch_one("SELECT COUNT(*) AS count FROM learning_credit_entries")["count"] == before
        decisions = fetch_all(
            "SELECT decision_type, target_type FROM learning_credit_import_decisions WHERE batch_id=?",
            (batch_id,),
        )
        assert decisions == [{"decision_type": "PERIOD_YEAR_ACCEPT", "target_type": "import_items"}]
    finally:
        _cleanup(batch_id)
        if 'created_member' in locals() and created_member:
            with transaction() as connection:
                execute(connection, "DELETE FROM members WHERE id=?", (member_id,))


def test_bulk_confirmation_aborts_when_snapshot_fingerprint_changes() -> None:
    result = register_suzhou_credit_workbook(
        content=__import__("test_g5_4_c0_c1")._small_workbook(), original_filename="fingerprint-review.xlsx"
    )
    batch_id = int(result["batch"]["id"])
    try:
        member_id, created_member = _ensure_member()
        with transaction() as connection:
            execute(
                connection,
                "UPDATE learning_credit_import_rows SET matched_member_id=?, match_status='AUTO_MATCHED', "
                "match_snapshot_id='snapshot-a', match_snapshot_fingerprint='fingerprint-a', "
                "match_algorithm_version='c2b-test-v1' WHERE batch_id=?",
                (member_id, batch_id),
            )
        with pytest.raises(ValueError, match="快照指纹"):
            bulk_confirm_high_confidence_matches(
                batch_id=batch_id, expected_count=2, snapshot_id="snapshot-b",
                snapshot_fingerprint="fingerprint-b", algorithm_version="c2b-test-v1",
                actor_user_id=_admin_id(), reason="指纹变化应阻断",
            )
    finally:
        _cleanup(batch_id)
        if 'created_member' in locals() and created_member:
            with transaction() as connection:
                execute(connection, "DELETE FROM members WHERE id=?", (member_id,))


def test_credit_total_source_correction_preserves_raw_null() -> None:
    result = register_suzhou_credit_workbook(
        content=__import__("test_g5_4_c0_c1")._small_workbook(), original_filename="source-correction-review.xlsx"
    )
    batch_id = int(result["batch"]["id"])
    try:
        row = fetch_one(
            "SELECT id, raw_total_points FROM learning_credit_import_rows WHERE batch_id=? AND validation_status='TOTAL_MISSING'",
            (batch_id,),
        )
        assert row and row["raw_total_points"] is None
        resolved = __import__("app.services.historical_credit_review", fromlist=["resolve_credit_anomalies"]).resolve_credit_anomalies(
            batch_id=batch_id, row_ids=[int(row["id"])], resolution="NEEDS_SOURCE_CORRECTION",
            actor_user_id=_admin_id(), reason="来源表需补充原始总分",
        )
        assert resolved["resolved_count"] == 1
        after = fetch_one(
            "SELECT raw_total_points, resolved_total_points, credit_review_status FROM learning_credit_import_rows WHERE id=?",
            (row["id"],),
        )
        assert after == {
            "raw_total_points": None,
            "resolved_total_points": None,
            "credit_review_status": "NEEDS_SOURCE_CORRECTION",
        }
    finally:
        _cleanup(batch_id)


def test_bulk_identity_and_credit_anomaly_decisions_are_atomic_and_replayable() -> None:
    result = register_suzhou_credit_workbook(
        content=__import__("test_g5_4_c0_c1")._small_workbook(), original_filename="review-workbench.xlsx"
    )
    batch_id = int(result["batch"]["id"])
    try:
        member_id, created_member = _ensure_member()
        with transaction() as connection:
            execute(
                connection,
                "UPDATE learning_credit_import_rows SET matched_member_id=?, match_status='AUTO_MATCHED' WHERE batch_id=? AND id IN "
                "(SELECT id FROM learning_credit_import_rows WHERE batch_id=? ORDER BY id LIMIT 2)",
                (member_id, batch_id, batch_id),
            )
            execute(
                connection,
                "UPDATE learning_credit_import_rows SET match_snapshot_id=?, match_snapshot_fingerprint=?, "
                "match_algorithm_version=? WHERE batch_id=? AND id IN "
                "(SELECT id FROM learning_credit_import_rows WHERE batch_id=? ORDER BY id LIMIT 2)",
                ("snapshot-test", "fingerprint-test", "c2b-test-v1", batch_id, batch_id),
            )
            execute(
                connection,
                "UPDATE learning_credit_import_items SET matched_member_id=? WHERE batch_id=? AND import_row_id IN "
                "(SELECT id FROM learning_credit_import_rows WHERE batch_id=? ORDER BY id LIMIT 2)",
                (member_id, batch_id, batch_id),
            )
        bulk = bulk_confirm_high_confidence_matches(
            batch_id=batch_id, expected_count=2, snapshot_id="snapshot-test",
            snapshot_fingerprint="fingerprint-test", algorithm_version="c2b-test-v1",
            actor_user_id=_admin_id(), reason="隔离快照高置信度批量确认",
        )
        repeated = bulk_confirm_high_confidence_matches(
            batch_id=batch_id, expected_count=2, snapshot_id="snapshot-test",
            snapshot_fingerprint="fingerprint-test", algorithm_version="c2b-test-v1",
            actor_user_id=_admin_id(), reason="重复请求应幂等",
        )
        assert bulk["confirmed_count"] == 2
        assert repeated["decision"]["idempotent"] is True
        total_missing = fetch_one(
            "SELECT id FROM learning_credit_import_rows WHERE batch_id=? AND validation_status='TOTAL_MISSING'",
            (batch_id,),
        )
        zero = fetch_one(
            "SELECT id FROM learning_credit_import_rows WHERE batch_id=? AND validation_status='ZERO'",
            (batch_id,),
        )
        assert total_missing and zero
        approved = approve_calculated_totals(
            batch_id=batch_id, row_ids=[int(total_missing["id"])], actor_user_id=_admin_id(),
            reason="明细单元格合计已复核",
        )
        no_credit = confirm_no_credit(
            batch_id=batch_id, row_ids=[int(zero["id"])], actor_user_id=_admin_id(),
            reason="来源明确为零分，不生成零分流水",
        )
        assert approved["approved_count"] == 1
        assert no_credit["confirmed_count"] == 1
        workbench = get_historical_credit_review_workbench(batch_id)
        assert len(workbench["decisions"]) == 3
        assert fetch_one(
            "SELECT credit_review_status FROM learning_credit_import_rows WHERE id=?",
            (total_missing["id"],),
        )["credit_review_status"] == "APPROVED"
        assert fetch_one(
            "SELECT credit_review_status FROM learning_credit_import_rows WHERE id=?",
            (zero["id"],),
        )["credit_review_status"] == "NO_CREDIT_CONFIRMED"
    finally:
        _cleanup(batch_id)
        if 'created_member' in locals() and created_member:
            with transaction() as connection:
                execute(connection, "DELETE FROM members WHERE id=?", (member_id,))


def test_class_mapping_candidates_are_persisted_without_auto_confirming_aliases() -> None:
    result = register_suzhou_credit_workbook(content=_year_only_workbook(), original_filename="mapping-workbench.xlsx")
    batch_id = int(result["batch"]["id"])
    try:
        saved = upsert_class_mapping_candidates(
            batch_id=batch_id,
            mappings=[
                {
                    "source_sheet": "姑苏相城",
                    "raw_class_name": "不一班",
                    "org_unit_id": None,
                    "mapping_status": "AMBIGUOUS",
                    "mapping_reason": "UNCONFIRMED_PLATFORM_CLASS_ALIAS",
                    "candidate_org_unit_ids": ["candidate-a", "candidate-b"],
                }
            ],
            snapshot_id="snapshot-test", snapshot_fingerprint="fingerprint-test",
            actor_user_id=_admin_id(),
        )
        assert saved["mapping_count"] == 1
        workbench = get_historical_credit_review_workbench(batch_id)
        assert workbench["class_mappings"][0]["mapping_status"] == "AMBIGUOUS"
        assert workbench["class_mappings"][0]["confirmed_org_unit_id"] is None
    finally:
        _cleanup(batch_id)
