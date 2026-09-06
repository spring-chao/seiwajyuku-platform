from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db import execute, fetch_one, transaction
from app.main import app
from app.services.attendance_scoring import upsert_score_record
from app.services.class_meeting_credits import (
    CLASS_MEETING_SCORE,
    dry_run_class_meeting_settlement,
    dry_run_class_meetings,
)
from test_v12_mvp import _seed_group_leader_fixture


@pytest.fixture(autouse=True)
def class_meeting_credit_test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _admin_id() -> int:
    return int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])


def _admin_headers() -> dict[str, str]:
    admin = fetch_one("SELECT id, token_version FROM app_users WHERE username='admin'")
    return {
        "Authorization": "Bearer "
        + create_token(
            admin["id"], admin["token_version"], "access", timedelta(minutes=5)
        )
    }


def _bind_class_meeting_rule(fixture: dict) -> None:
    binding = fetch_one(
        "SELECT b.id, b.credit_rule_version_id, p.plan_key, p.version_label "
        "FROM class_learning_bindings b "
        "JOIN learning_plan_versions p ON p.id=b.plan_version_id "
        "WHERE b.class_org_unit_id=? AND b.status='ACTIVE' LIMIT 1",
        (fixture["class_id"],),
    )
    assert binding
    now = _now()
    with transaction() as connection:
        version = execute(
            connection,
            "SELECT id FROM learning_credit_rule_versions "
            "WHERE rule_set_key=? AND version_label=? LIMIT 1",
            (binding["plan_key"], binding["version_label"]),
        ).fetchone()
        if not version:
            version_cursor = execute(
                connection,
                "INSERT INTO learning_credit_rule_versions "
                "(rule_set_key, version_label, status, created_at, updated_at) "
                "VALUES (?, ?, 'PUBLISHED', ?, ?)",
                (binding["plan_key"], binding["version_label"], now, now),
            )
            version_id = int(version_cursor.lastrowid)
        else:
            version_id = int(version["id"])
            execute(
                connection,
                "UPDATE learning_credit_rule_versions SET status='PUBLISHED', updated_at=? WHERE id=?",
                (now, version_id),
            )
        execute(
            connection,
            "INSERT OR IGNORE INTO learning_credit_rules "
            "(rule_version_id, rule_key, credit_category, credit_type, settlement_model, "
            "rule_snapshot_json, created_at, updated_at) "
            "VALUES (?, ?, 'STANDARD_LEARNING', ?, 'EVENT_ONCE', ?, ?, ?)",
            (
                version_id,
                CLASS_MEETING_SCORE,
                CLASS_MEETING_SCORE,
                '{"points":"FROM_ATTENDANCE_SCORE","source":"attendance_score_records"}',
                now,
                now,
            ),
        )
        execute(
            connection,
            "UPDATE class_learning_bindings SET credit_rule_version_id=? WHERE id=?",
            (version_id, binding["id"]),
        )


def _create_class_meeting(
    fixture: dict,
    *,
    member_ids: list[int] | None = None,
    event_date: str = "2026-09-06",
    scores: dict[int, dict[str, float]] | None = None,
    missing_scores: set[tuple[int, str]] | None = None,
) -> int:
    _bind_class_meeting_rule(fixture)
    member_ids = member_ids or [fixture["member_id"], fixture["other_member_id"]]
    scores = scores or {
        fixture["member_id"]: {"MORNING": 7, "AFTERNOON": 7, "KONPA": 4},
        fixture["other_member_id"]: {"MORNING": 6, "AFTERNOON": 7, "KONPA": 4},
    }
    missing_scores = missing_scores or set()
    now = _now()
    center_id = fetch_one(
        "SELECT parent_id FROM org_units WHERE id=?", (fixture["class_id"],)
    )["parent_id"]
    with transaction() as connection:
        group_cursor = execute(
            connection,
            "INSERT INTO attendance_event_groups "
            "(source_key, external_group_id, org_unit_id, study_org_unit_id, title, "
            "activity_type, event_date, status, created_at, updated_at) "
            "VALUES ('c6-test', ?, ?, ?, 'C6班会测试', 'CLASS_MEETING', ?, 'ACTIVE', ?, ?)",
            (
                uuid4().hex,
                center_id,
                fixture["class_id"],
                event_date,
                now,
                now,
            ),
        )
        group_id = int(group_cursor.lastrowid)
        record_ids: list[tuple[int, int, str, str]] = []
        for order, code in enumerate(("MORNING", "AFTERNOON", "KONPA"), start=1):
            session_cursor = execute(
                connection,
                "INSERT INTO attendance_sessions "
                "(event_group_id, external_session_id, session_code, session_name, "
                "session_order, scheduled_start_at, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
                (
                    group_id,
                    uuid4().hex,
                    code,
                    {"MORNING": "上午", "AFTERNOON": "下午", "KONPA": "空巴"}[code],
                    order,
                    f"{event_date}T09:00:00+00:00",
                    now,
                    now,
                ),
            )
            session_id = int(session_cursor.lastrowid)
            for member_id in member_ids:
                record_cursor = execute(
                    connection,
                    "INSERT INTO attendance_records "
                    "(attendance_session_id, external_record_id, member_id, "
                    "member_code_snapshot, name_snapshot, participant_type, score_eligible, "
                    "attendance_status, checked_at, received_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'MEMBER', 1, 'PRESENT', ?, ?, ?, ?)",
                    (
                        session_id,
                        uuid4().hex,
                        member_id,
                        f"C6-{member_id}",
                        f"C6学员{member_id}",
                        f"{event_date}T08:59:00+00:00",
                        now,
                        now,
                        now,
                    ),
                )
                record_ids.append((int(record_cursor.lastrowid), member_id, code, now))

    for record_id, member_id, code, source_updated_at in record_ids:
        if (member_id, code) in missing_scores:
            continue
        score = (scores.get(member_id) or {}).get(code, 0)
        rule = fetch_one(
            "SELECT id, rule_version, base_points FROM attendance_score_rules "
            "WHERE activity_type='CLASS_MEETING' AND session_code=? AND status='ACTIVE' "
            "ORDER BY rule_version DESC LIMIT 1",
            (code,),
        )
        assert rule
        upsert_score_record(
            attendance_record_id=record_id,
            member_id=member_id,
            session_code=code,
            attendance_status="PRESENT",
            checked_at=f"{event_date}T08:59:00+00:00",
            scheduled_start_at=f"{event_date}T09:00:00+00:00",
            score_eligible=True,
            source_updated_at=source_updated_at,
        )
        if float(score) != float(rule["base_points"]):
            with transaction() as connection:
                execute(
                    connection,
                    "UPDATE attendance_score_records SET final_points=?, "
                    "late_deduction=?, calculated_at=?, updated_at=? "
                    "WHERE attendance_record_id=?",
                    (
                        score,
                        float(rule["base_points"]) - float(score),
                        source_updated_at,
                        source_updated_at,
                        record_id,
                    ),
                )
    return group_id


def test_class_meeting_projection_uses_backend_breakdown_and_is_zero_write() -> None:
    fixture = _seed_group_leader_fixture()
    group_id = _create_class_meeting(fixture)
    before = int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"])

    preview = dry_run_class_meeting_settlement(
        actor_user_id=_admin_id(), event_group_id=group_id
    )

    assert preview["mode"] == "DRY_RUN"
    assert preview["persisted"] is False
    assert preview["settlement_enabled"] is False
    assert preview["formal_settlement_allowed"] is False
    assert preview["totals"]["proposed_points"] == 35
    assert preview["totals"]["proposed_entry_count"] == 2
    first = next(item for item in preview["entries"] if item["member_id"] == fixture["member_id"])
    assert first["morning_points"] == 7
    assert first["afternoon_points"] == 7
    assert first["konpa_points"] == 4
    assert first["final_points"] == 18
    assert first["proposed_points"] == 18
    assert first["status"] == "READY"
    assert first["idempotency_key"] == f"CLASS_MEETING:{fixture['member_id']}:{group_id}"
    assert first["rule_key"] == CLASS_MEETING_SCORE
    assert first["rule_version_id"] is not None
    assert preview["write_proof"] == {
        "writes_performed": False,
        "ledger_entries_before": before,
        "ledger_entries_after": before,
        "ledger_entries_delta": 0,
    }
    assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"] == before


def test_class_meeting_projection_is_idempotent_and_skips_existing_ledger_entry() -> None:
    fixture = _seed_group_leader_fixture()
    group_id = _create_class_meeting(fixture, member_ids=[fixture["member_id"]])
    preview = dry_run_class_meeting_settlement(
        actor_user_id=_admin_id(), event_group_id=group_id
    )
    item = preview["entries"][0]
    now = _now()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO learning_credit_entries "
            "(member_id, credit_category, credit_type, points, source_type, source_id, "
            "class_org_unit_id, rule_key, rule_version, rule_version_id, rule_snapshot_json, "
            "occurred_at, status, idempotency_key, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'POSTED', ?, ?, ?)",
            (
                item["member_id"],
                item["credit_category"],
                item["credit_type"],
                item["points"],
                item["source_type"],
                item["source_id"],
                item["class_org_unit_id"],
                item["rule_key"],
                item["rule_version"],
                item["rule_version_id"],
                "{}",
                item["occurred_at"],
                item["idempotency_key"],
                now,
                now,
            ),
        )
    repeated = dry_run_class_meeting_settlement(
        actor_user_id=_admin_id(), event_group_id=group_id
    )
    assert repeated["entries"][0]["status"] == "SKIPPED_DUPLICATE"
    assert repeated["entries"][0]["postable"] is False
    assert repeated["entries"][0]["existing_entry"]["idempotency_key"] == item["idempotency_key"]

    for _ in range(9):
        assert dry_run_class_meeting_settlement(
            actor_user_id=_admin_id(), event_group_id=group_id
        ) == repeated


def test_class_meeting_projection_follows_latest_score_before_post() -> None:
    fixture = _seed_group_leader_fixture()
    group_id = _create_class_meeting(fixture, member_ids=[fixture["member_id"]])
    initial = dry_run_class_meeting_settlement(
        actor_user_id=_admin_id(), event_group_id=group_id
    )
    assert initial["entries"][0]["final_points"] == 18
    score_record_id = initial["entries"][0]["score_details"][0]["score_record_id"]
    with transaction() as connection:
        execute(
            connection,
            "UPDATE attendance_score_records SET final_points=5, "
            "calculated_at=?, updated_at=? WHERE id=?",
            ("2100-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00", score_record_id),
        )
    changed = dry_run_class_meeting_settlement(
        actor_user_id=_admin_id(), event_group_id=group_id
    )
    assert changed["entries"][0]["final_points"] == 16
    assert changed["entries"][0]["morning_points"] == 5
    assert changed["entries"][0]["status"] == "READY"


def test_class_meeting_projection_blocks_missing_score_without_recalculating() -> None:
    fixture = _seed_group_leader_fixture()
    group_id = _create_class_meeting(
        fixture,
        member_ids=[fixture["member_id"]],
        missing_scores={(fixture["member_id"], "AFTERNOON")},
    )
    before = int(fetch_one("SELECT COUNT(*) AS n FROM attendance_score_records")["n"])
    preview = dry_run_class_meeting_settlement(
        actor_user_id=_admin_id(), event_group_id=group_id
    )
    item = preview["entries"][0]
    assert item["status"] == "BLOCKED"
    assert item["postable"] is False
    assert any("下午缺少最终评分记录" in reason for reason in item["reasons"])
    assert fetch_one("SELECT COUNT(*) AS n FROM attendance_score_records")["n"] == before


def test_class_meeting_projection_supports_no_credit_and_class_date_batch() -> None:
    fixture = _seed_group_leader_fixture()
    no_credit_group = _create_class_meeting(
        fixture,
        member_ids=[fixture["member_id"]],
        scores={fixture["member_id"]: {"MORNING": 0, "AFTERNOON": 0, "KONPA": 0}},
        event_date="2026-09-07",
    )
    ready_group = _create_class_meeting(
        fixture,
        member_ids=[fixture["member_id"]],
        event_date="2026-09-08",
    )
    no_credit = dry_run_class_meeting_settlement(
        actor_user_id=_admin_id(), event_group_id=no_credit_group
    )
    assert no_credit["entries"][0]["status"] == "NO_CREDIT"
    assert no_credit["totals"]["proposed_entry_count"] == 0

    batch = dry_run_class_meetings(
        actor_user_id=_admin_id(),
        class_org_unit_id=fixture["class_id"],
        event_date_from="2026-09-07",
        event_date_to="2026-09-08",
        limit=10,
    )
    assert batch["persisted"] is False
    assert {item["meeting"]["id"] for item in batch["meetings"]} == {
        no_credit_group,
        ready_group,
    }
    assert batch["totals"]["proposed_points"] == 18
    assert batch["write_proof"]["ledger_entries_delta"] == 0


def test_class_meeting_api_exposes_single_preview_with_preview_permission() -> None:
    fixture = _seed_group_leader_fixture()
    group_id = _create_class_meeting(fixture, member_ids=[fixture["member_id"]])
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/learning-credits/dry-run/class-meetings/{group_id}",
            headers=_admin_headers(),
        )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["meeting"]["id"] == group_id
    assert data["entries"][0]["final_points"] == 18
    assert data["persisted"] is False


def test_class_meeting_correction_is_append_only_after_future_post() -> None:
    fixture = _seed_group_leader_fixture()
    group_id = _create_class_meeting(fixture, member_ids=[fixture["member_id"]])
    initial = dry_run_class_meeting_settlement(
        actor_user_id=_admin_id(), event_group_id=group_id
    )
    original_item = initial["entries"][0]

    from app.services.learning_credits import (
        member_credit_summary,
        post_credit_entry,
        reverse_credit_entry,
    )

    with pytest.MonkeyPatch.context() as patch_env:
        patch_env.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
        original = post_credit_entry(actor_user_id=_admin_id(), item=original_item)
        reverse_credit_entry(
            actor_user_id=_admin_id(),
            entry_id=original["id"],
            reason="班会评分修正，先冲销原入账",
        )

        score_record_id = original_item["score_details"][0]["score_record_id"]
        with transaction() as connection:
            execute(
                connection,
                "UPDATE attendance_score_records SET final_points=5, "
                "calculated_at=?, updated_at=? WHERE id=?",
                ("2100-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00", score_record_id),
            )
        corrected_preview = dry_run_class_meeting_settlement(
            actor_user_id=_admin_id(), event_group_id=group_id
        )
        corrected = corrected_preview["entries"][0]
        assert corrected["status"] == "SKIPPED_DUPLICATE"
        assert corrected["final_points"] == 16

        corrected_item = {
            **corrected,
            "idempotency_key": f"{corrected['idempotency_key']}:CORRECTION:2100-01-01",
        }
        replacement = post_credit_entry(
            actor_user_id=_admin_id(), item=corrected_item
        )

    assert replacement["points"] == 16
    assert fetch_one(
        "SELECT status, points FROM learning_credit_entries WHERE id=?",
        (original["id"],),
    ) == {"status": "REVERSED", "points": 18.0}
    assert member_credit_summary(
        actor_user_id=_admin_id(), member_id=fixture["member_id"]
    )["total_points"] == 16


def test_class_meeting_projection_rejects_invalid_date_window() -> None:
    with pytest.raises(ValueError, match="开始日期不能晚于结束日期"):
        dry_run_class_meetings(
            actor_user_id=_admin_id(),
            event_date_from="2026-09-07",
            event_date_to="2026-09-06",
        )
