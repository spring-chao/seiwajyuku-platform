from __future__ import annotations

from datetime import UTC, datetime

from app.db import execute, fetch_one, transaction
from app.services.class_operations import (
    class_operations_detail,
    update_class_operations,
)
from app.services.plans import operations_snapshot


def test_class_operations_profile_and_snapshot_class_count() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "INSERT OR IGNORE INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES ('class-ops-center','CLASS_OPS_CENTER','班级运营测试中心','REGIONAL_CENTER','org-suzhou',1,?,?)",
            (now, now),
        )
        execute(
            connection,
            "INSERT OR IGNORE INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES ('class-ops-class','CLASS_OPS_CLASS','班级运营测试班','CLASS','class-ops-center',1,?,?)",
            (now, now),
        )
        execute(
            connection,
            "INSERT OR IGNORE INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES ('class-ops-group','CLASS_OPS_GROUP','班级运营测试组','GROUP','class-ops-class',1,?,?)",
            (now, now),
        )

    updated = update_class_operations(
        actor_user_id=admin["id"], class_org_unit_id="class-ops-class",
        year=2026, month=8,
        updates={
            "weekly_meeting_at": "2026-08-03 19:00:00",
            "planned_class_meeting_at": "2026-08-22 09:00:00",
            "learning_month": 6,
            "learning_progress": "经营十二条第 4 条",
            "revenue_growing_member_count": 3,
            "revenue_comparable_member_count": 5,
            "groups": [{
                "group_org_unit_id": "class-ops-group",
                "planned_meeting_at": "2026-08-12 19:30:00",
            }],
        },
    )
    assert updated["learning_month"] == 6
    assert updated["learning_progress"] == "经营十二条第 4 条"
    assert updated["revenue_growth_ratio"] == 0.6
    assert updated["groups"][0]["planned_meeting_at"] == "2026-08-12 19:30:00"
    assert updated["class_attendance"]["rate"] is None

    detail = class_operations_detail(
        user_id=admin["id"], class_org_unit_id="class-ops-class",
        year=2026, month=8,
    )
    assert detail["weekly_meeting_at"] == "2026-08-03 19:00:00"

    snapshot = operations_snapshot(user_id=admin["id"], year=2026, month=8)
    assert snapshot["summary"]["class_count"] == len(snapshot["classes"])
    assert any(row["class_name"] == "班级运营测试班" for row in snapshot["classes"])


def test_class_operations_rejects_invalid_revenue_ratio() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    try:
        update_class_operations(
            actor_user_id=admin["id"], class_org_unit_id="class-ops-class",
            year=2026, month=8,
            updates={
                "revenue_growing_member_count": 6,
                "revenue_comparable_member_count": 5,
                "groups": [],
            },
        )
    except ValueError as exc:
        assert "不能大于" in str(exc)
    else:
        raise AssertionError("应拒绝不合法的业绩增长人数")
