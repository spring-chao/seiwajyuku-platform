"""Disposable MySQL integration gate for historical credit settlement batches."""

from __future__ import annotations

from io import BytesIO
import os
from unittest.mock import patch
from uuid import uuid4

import pytest
from openpyxl import Workbook

from app.db import execute, fetch_one, transaction
from app.services.credit_settlement_batches import (
    approve_historical_credit_batch,
    dry_run_historical_credit_batches,
    post_historical_credit_batch,
    submit_historical_credit_batch_for_approval,
)
from app.services.historical_credit_import import register_suzhou_credit_workbook
from app.services.historical_credit_review import accept_year_only_period, upsert_class_mapping_candidates
from credit_batch_test_support import create_credit_batch_reviewer
from test_learning_credit_ledger import _admin_id
from test_v12_mvp import _seed_group_leader_fixture


pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("mysql+pymysql://"),
    reason="historical batch MySQL gate requires a disposable isolated MySQL runtime",
)


def _history_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "历史结算测试班"
    sheet.append(["历史学分测试"])
    sheet.append(["序号", "姓名", "组别", "学习内容", "每日读书", "班级学习日"])
    sheet.append([None, None, None, "总分值", "1月", "班级学习日"])
    sheet.append([None, None, None, None, 1, 4])
    sheet.append([1, "历史测试学员", "1组", 5, 1, 4])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_historical_credit_batch_mysql_preserves_year_precision_and_gated_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEARNING_CREDIT_BATCH_DRY_RUN_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CREDIT_BATCH_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")
    monkeypatch.setenv("LEARNING_CREDIT_BATCH_POST_ENABLED", "false")
    monkeypatch.setenv("LEARNING_CREDIT_HISTORICAL_POST_ENABLED", "false")

    fixture = _seed_group_leader_fixture()
    filename = f"historical-mysql-{uuid4().hex}.xlsx"
    imported = register_suzhou_credit_workbook(
        content=_history_workbook(), original_filename=filename,
    )
    import_batch_id = int(imported["batch"]["id"])
    actor = _admin_id()
    class_id = str(fixture["class_id"])
    member_id = int(fixture["member_id"])
    with transaction() as connection:
        execute(
            connection,
            "UPDATE learning_credit_import_rows SET matched_member_id=?,match_status='AUTO_MATCHED',"
            "match_snapshot_id='mysql-snapshot',match_snapshot_fingerprint='mysql-fingerprint',"
            "match_algorithm_version='mysql-test-v1' WHERE batch_id=?",
            (member_id, import_batch_id),
        )
        execute(
            connection,
            "UPDATE learning_credit_import_items SET matched_member_id=? WHERE batch_id=?",
            (member_id, import_batch_id),
        )
    upsert_class_mapping_candidates(
        batch_id=import_batch_id,
        mappings=[{
            "source_sheet": "历史结算测试班", "raw_class_name": "历史结算测试班",
            "org_unit_id": class_id, "mapping_status": "EXACT",
            "mapping_reason": "CI_EXACT_CLASS", "candidate_org_unit_ids": [class_id],
        }],
        snapshot_id="mysql-snapshot", snapshot_fingerprint="mysql-fingerprint",
        actor_user_id=actor,
    )
    accepted = accept_year_only_period(
        batch_id=import_batch_id, expected_count=1, actor_user_id=actor,
        reason="CI source proves year precision only",
    )
    assert accepted["accepted_count"] == 1

    ledger_before = int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"])
    result = dry_run_historical_credit_batches(actor_user_id=actor, import_batch_id=import_batch_id)
    assert result["ready_item_count"] == 2
    assert result["ready_points"] == "5.00"
    assert result["learning_credit_entries_delta"] == 0
    month_batch = next(
        batch for batch in result["settlement_batches"]
        if fetch_one(
            "SELECT period_precision FROM learning_credit_settlement_batches WHERE id=?",
            (batch["id"],),
        )["period_precision"] == "MONTH"
    )
    month_item = fetch_one(
        "SELECT occurred_at,occurred_precision,occurred_year,occurred_month,rule_version_id "
        "FROM learning_credit_settlement_batch_items WHERE batch_id=?",
        (month_batch["id"],),
    )
    assert month_item == {
        "occurred_at": None, "occurred_precision": "MONTH", "occurred_year": 2026,
        "occurred_month": 1, "rule_version_id": None,
    }
    submitted = submit_historical_credit_batch_for_approval(actor_user_id=actor, batch_id=month_batch["id"])
    assert submitted["status"] == "PENDING_APPROVAL"
    reviewer = create_credit_batch_reviewer()
    with patch(
        "app.services.learning_credits.user_context",
        return_value={"permissions": [
            "plans:credit_settlement_approve", "plans:historical_credit_import_manage",
        ]},
    ), patch("app.services.learning_credits.accessible_org_ids", return_value=None):
        approved = approve_historical_credit_batch(actor_user_id=reviewer, batch_id=month_batch["id"])
    assert approved["status"] == "APPROVED"
    assert int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]) == ledger_before

    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CREDIT_BATCH_POST_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CREDIT_HISTORICAL_POST_ENABLED", "true")
    with patch(
        "app.services.learning_credits.user_context",
        return_value={"permissions": [
            "plans:credit_settlement_manage", "plans:credit_settlement_post",
            "plans:historical_credit_import_manage",
        ]},
    ):
        posted = post_historical_credit_batch(actor_user_id=actor, batch_id=month_batch["id"])
        replay = post_historical_credit_batch(actor_user_id=actor, batch_id=month_batch["id"])
    assert posted["status"] == "POSTED"
    assert posted["posted_entry_count"] == 1
    assert replay["idempotent"] is True
    entry = fetch_one(
        "SELECT source_type,rule_version,points,occurred_at,occurred_precision,occurred_year,occurred_month "
        "FROM learning_credit_entries WHERE idempotency_key=(SELECT idempotency_key "
        "FROM learning_credit_settlement_batch_items WHERE batch_id=? LIMIT 1)",
        (month_batch["id"],),
    )
    assert entry == {
        "source_type": "LEGACY_SUZHOU_2026_V1", "rule_version": "LEGACY_SUZHOU_2026_V1",
        "points": 1, "occurred_at": None, "occurred_precision": "MONTH",
        "occurred_year": 2026, "occurred_month": 1,
    }
    assert int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]) == ledger_before + 1
