"""Read-only settlement workbench views, scope checks, and privacy shape."""

from __future__ import annotations

import json
import hashlib
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.db import execute, fetch_one, transaction
from app.services.learning_credits import _insert_entry, credit_ledger_overview
from app.services.credit_settlement_batches import (
    get_settlement_batch,
    list_settlement_batches,
)
from test_learning_credit_ledger import _admin_id
from test_v12_mvp import _seed_group_leader_fixture


def _insert_batch(fixture: dict) -> int:
    now = "2026-09-29T12:00:00+00:00"
    suffix = uuid4().hex
    source_fingerprint = hashlib.sha256(suffix.encode("ascii")).hexdigest()
    rule_fingerprint = hashlib.sha256((suffix + "-rules").encode("ascii")).hexdigest()
    with transaction() as connection:
        execute(
            connection,
            "UPDATE members SET name=? WHERE id=?",
            ("张三丰", fixture["member_id"]),
        )
        batch = execute(
            connection,
            "INSERT INTO learning_credit_settlement_batches "
            "(batch_no,batch_type,source_type,class_org_unit_id,period_precision,period_start,period_end,status,"
            "proposed_entry_count,proposed_points,blocked_count,source_snapshot_json,rule_snapshot_json,"
            "result_snapshot_json,source_fingerprint,rule_fingerprint,created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,'DRY_RUN',1,'2.00',0,?,?,?,?,?,?,?,?)",
            (
                f"LC-API-{suffix}", "REGULAR", "STUDY_MEETING", fixture["class_id"],
                "EXACT_DATE", "2026-09-01", "2026-09-01", json.dumps({"private": "omit"}),
                "{}", json.dumps({
                    "duplicate_entry_count": 2,
                    "private_result": f"result-private-{suffix}",
                }),
                source_fingerprint, rule_fingerprint, _admin_id(), now, now,
            ),
        )
        batch_id = int(batch.lastrowid)
        execute(
            connection,
            "INSERT INTO learning_credit_settlement_batch_items "
            "(batch_id,member_id,source_type,source_id,source_snapshot_json,rule_key,rule_version,"
            "rule_snapshot_json,credit_category,credit_type,points,occurred_at,occurred_precision,"
            "occurred_year,occurred_month,idempotency_key,status,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'2026-09-01','EXACT_DATE',2026,9,?,'PROPOSED',?,?)",
            (
                batch_id, fixture["member_id"], "STUDY_MEETING", f"private-source-{suffix}",
                json.dumps({"private": "omit"}), "GROUP_MEETING_ATTENDANCE", "2026.1", "{}",
                "STANDARD_LEARNING", "GROUP_MEETING_ATTENDANCE", "2.00", f"api-test-{suffix}", now, now,
            ),
        )
        return batch_id


def test_list_settlement_batches_returns_only_operator_summary_and_gates(monkeypatch) -> None:
    fixture = _seed_group_leader_fixture()
    batch_id = _insert_batch(fixture)
    hidden_fixture = _seed_group_leader_fixture()
    _insert_batch(hidden_fixture)
    monkeypatch.setattr(
        "app.services.credit_settlement_batches.credits.user_context",
        lambda _actor: {"permissions": ["plans:credit_settlement_manage"]},
    )
    monkeypatch.setattr(
        "app.services.credit_settlement_batches.credits.accessible_org_ids",
        lambda _actor: {fixture["class_id"]},
    )
    monkeypatch.setattr(
        "app.services.credit_settlement_batches.credits.get_settings",
        lambda: SimpleNamespace(
            learning_credit_batch_dry_run_enabled=False,
            learning_credit_batch_approval_enabled=False,
            learning_credit_batch_post_enabled=False,
            learning_credit_settlement_enabled=False,
            learning_credit_historical_post_enabled=False,
        ),
    )

    result = list_settlement_batches(actor_user_id=_admin_id())
    assert result["storage_available"] is True
    assert result["total_count"] == 1
    assert result["status_counts"]["DRY_RUN"] == 1
    assert result["feature_gates"] == {
        "dry_run_enabled": False,
        "approval_enabled": False,
        "post_enabled": False,
        "settlement_enabled": False,
        "historical_post_enabled": False,
    }
    batch = next(row for row in result["batches"] if row["id"] == batch_id)
    assert batch["status"] == "DRY_RUN"
    assert batch["proposed_points"] == "2.00"
    assert batch["duplicate_entry_count"] == 2
    assert "source_snapshot_json" not in batch
    assert "result_snapshot" not in batch
    assert "private_result" not in json.dumps(batch)


def test_credit_ledger_overview_aggregates_only_visible_posted_entries(monkeypatch) -> None:
    fixture = _seed_group_leader_fixture()
    hidden_fixture = _seed_group_leader_fixture()
    actor_id = _admin_id()

    def add_entry(target: dict, *, points: str, category: str, credit_type: str, key: str) -> None:
        with transaction() as connection:
            _insert_entry(
                connection,
                {
                    "member_id": target["member_id"],
                    "credit_category": category,
                    "credit_type": credit_type,
                    "points": points,
                    "source_type": "STUDY_MEETING",
                    "source_id": key,
                    "class_org_unit_id": target["class_id"],
                    "learning_cycle_id": None,
                    "rule_key": "TEST_RULE",
                    "rule_version": "test-v1",
                    "rule_version_id": None,
                    "rule_snapshot": {"rule_key": "TEST_RULE"},
                    "occurred_at": "2026-09-01",
                    "occurred_precision": "EXACT_DATE",
                    "occurred_year": 2026,
                    "occurred_month": 9,
                    "idempotency_key": key,
                    "reversal_of_entry_id": None,
                },
                status="POSTED",
                actor_user_id=actor_id,
            )

    add_entry(
        fixture, points="2.50", category="STANDARD_LEARNING",
        credit_type="GROUP_MEETING_ATTENDANCE", key="overview-visible-standard",
    )
    add_entry(
        fixture, points="3.25", category="EXTENSION_ACTIVITY",
        credit_type="EXCELLENT_SHARE", key="overview-visible-extension",
    )
    add_entry(
        hidden_fixture, points="99.00", category="STANDARD_LEARNING",
        credit_type="GROUP_MEETING_ATTENDANCE", key="overview-hidden",
    )
    monkeypatch.setattr(
        "app.services.learning_credits.user_context",
        lambda _actor: {"permissions": ["plans:credit_settlement_preview"]},
    )
    monkeypatch.setattr(
        "app.services.learning_credits.accessible_org_ids",
        lambda _actor: {fixture["class_id"]},
    )

    result = credit_ledger_overview(actor_user_id=actor_id)

    assert result["entry_count"] == 2
    assert result["total_points"] == "5.75"
    assert result["categories"] == [
        {"credit_category": "EXTENSION_ACTIVITY", "entry_count": 1, "points": "3.25"},
        {"credit_category": "STANDARD_LEARNING", "entry_count": 1, "points": "2.50"},
    ]
    assert {item["credit_type"]: item["points"] for item in result["credit_types"]} == {
        "EXCELLENT_SHARE": "3.25",
        "GROUP_MEETING_ATTENDANCE": "2.50",
    }


def test_settlement_batch_detail_masks_member_and_omits_raw_snapshots(monkeypatch) -> None:
    fixture = _seed_group_leader_fixture()
    batch_id = _insert_batch(fixture)
    ledger_before = int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"])
    monkeypatch.setattr(
        "app.services.credit_settlement_batches.credits.user_context",
        lambda _actor: {"permissions": ["plans:credit_settlement_manage"]},
    )
    monkeypatch.setattr(
        "app.services.credit_settlement_batches.credits.accessible_org_ids",
        lambda _actor: {fixture["class_id"]},
    )

    result = get_settlement_batch(actor_user_id=_admin_id(), batch_id=batch_id)
    item = result["items"][0]
    assert result["id"] == batch_id
    assert item["member_name_masked"] == "张*丰"
    assert item["source_ref"] == {"source_type": "STUDY_MEETING"}
    assert "private-source" not in json.dumps(item)
    assert "source_snapshot_json" not in item
    assert "rule_snapshot_json" not in item
    assert "result_snapshot" not in result
    assert "private_result" not in json.dumps(result)
    assert int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]) == ledger_before
