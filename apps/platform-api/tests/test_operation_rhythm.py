from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.services import operation_rhythm as operation_rhythm_service
from app.db import execute, fetch_all, fetch_one, transaction
from app.services.iam import create_user
from app.services.operation_rhythm import (
    generate_rhythm_cycles,
    rhythm_snapshot,
    update_rhythm_item,
)


def _insert_scope() -> tuple[str, str, int, int]:
    suffix = uuid4().hex[:8]
    center_id = f"rhythm-center-{suffix}"
    class_id = f"rhythm-class-{suffix}"
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, '节奏测试分中心', 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
            (center_id, f"RHYTHM_CENTER_{suffix}", now, now),
        )
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, '节奏测试班', 'CLASS', ?, 1, ?, ?)",
            (class_id, f"RHYTHM_CLASS_{suffix}", center_id, now, now),
        )
        member_id = execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, birthday, join_date, created_at, updated_at) "
            "VALUES (?, '节奏测试学长', ?, 'ACTIVE', '1980-08-26', '2021-03-18', ?, ?)",
            (f"RHYTHM_MEMBER_{suffix}", center_id, now, now),
        ).lastrowid
        for relation_type, org_id in (
            ("PRIMARY_REGION", center_id),
            ("STUDY_CLASS", class_id),
        ):
            execute(
                connection,
                "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, source_type, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, 'TEST', ?, ?)",
                (member_id, org_id, relation_type, now, now),
            )
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    user_id = create_user(
        admin["id"],
        username=f"rhythm-user-{suffix}",
        display_name="运营节奏测试",
        password="rhythm-test-password",
        roles=["ops_center_operations"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": center_id}],
    )
    return center_id, class_id, member_id, user_id


def test_generate_rhythm_is_idempotent_and_expands_birthday_care() -> None:
    _, class_id, member_id, user_id = _insert_scope()

    first = generate_rhythm_cycles(user_id, 2026, 8)
    second = generate_rhythm_cycles(user_id, 2026, 8)

    assert first["cycle_count"] == 1
    assert first["created_item_count"] == 10
    assert second["created_item_count"] == 0
    rows = fetch_all(
        "SELECT item_key, due_date, start_date, business_type, business_id FROM operation_items "
        "WHERE org_unit_id=? AND period='2026-08' ORDER BY item_key",
        (class_id,),
    )
    birthday = next(row for row in rows if row["business_type"] == "BIRTHDAY_CARE")
    prep = next(row for row in rows if row["item_key"] == "CLASS_MEETING_PREPARATION")
    credit = next(row for row in rows if row["item_key"] == "CREDIT_SUBMISSION")
    assert birthday["business_id"] == str(member_id)
    assert birthday["due_date"] == "2026-08-26"
    assert prep["start_date"] == "2026-08-21"
    assert prep["due_date"] == "2026-08-27"
    assert credit["due_date"] == "2026-09-02"

    snapshot = rhythm_snapshot(user_id, 2026, 8)
    assert snapshot["data_quality"]["generated"] is True
    assert snapshot["summary"]["total"] == 10
    assert any(item["business_type"] == "BIRTHDAY_CARE" for item in snapshot["items"])


def test_snapshot_normalizes_mysql_date_values(monkeypatch) -> None:
    _, _, _, user_id = _insert_scope()
    generate_rhythm_cycles(user_id, 2026, 8)
    original_fetch_all = operation_rhythm_service.fetch_all

    def mysql_date_fetch_all(sql: str, params=()):
        rows = original_fetch_all(sql, params)
        if "FROM operation_items i JOIN org_units" in sql:
            for row in rows:
                for field in ("start_date", "due_date"):
                    if row.get(field):
                        row[field] = date.fromisoformat(row[field])
        return rows

    monkeypatch.setattr(operation_rhythm_service, "fetch_all", mysql_date_fetch_all)

    snapshot = rhythm_snapshot(user_id, 2026, 8)

    assert snapshot["summary"]["total"] == 10
    assert all(
        item["due_date"] is None or isinstance(item["due_date"], str)
        for item in snapshot["items"]
    )


def test_update_rhythm_item_records_status_and_audited_note() -> None:
    _, class_id, _, user_id = _insert_scope()
    generate_rhythm_cycles(user_id, 2026, 8)
    item = fetch_one(
        "SELECT id FROM operation_items WHERE org_unit_id=? AND business_type='BIRTHDAY_CARE'",
        (class_id,),
    )
    assert item
    result = update_rhythm_item(
        user_id,
        item["id"],
        status="COMPLETED",
        note="已通过微信群完成生日关怀，文案已人工确认。",
    )
    assert result["status"] == "COMPLETED"
    progress = fetch_one(
        "SELECT status, note, source_type FROM operation_progress_records WHERE item_id=? ORDER BY id DESC LIMIT 1",
        (item["id"],),
    )
    assert progress == {
        "status": "COMPLETED",
        "note": "已通过微信群完成生日关怀，文案已人工确认。",
        "source_type": "MANUAL",
    }


def test_update_rhythm_item_rejects_out_of_scope_item() -> None:
    _, class_id, _, scoped_user_id = _insert_scope()
    _, other_class_id, _, other_user_id = _insert_scope()
    generate_rhythm_cycles(other_user_id, 2026, 8)
    item = fetch_one(
        "SELECT id FROM operation_items WHERE org_unit_id=? AND item_key='CLASS_MEETING' LIMIT 1",
        (other_class_id,),
    )
    assert item
    with pytest.raises(PermissionError):
        update_rhythm_item(scoped_user_id, item["id"], status="ATTENTION")
    assert fetch_one(
        "SELECT status FROM operation_items WHERE id=?", (item["id"],)
    )["status"] == "PLANNED"
