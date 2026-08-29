from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import execute, fetch_one, transaction
from app.main import app
from app.services.members import update_member
from app.services.volunteer_positions import (
    get_member_volunteer_services,
    set_member_current_volunteer_position,
)
from app.services.legacy_volunteer_adoption import (
    LEGACY_POSITION_AUTO_ADOPT_CONFIRMATION,
    LEGACY_POSITION_AUTO_ADOPT_SOURCE,
    apply_legacy_volunteer_adoption,
    preview_legacy_volunteer_adoption,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _admin_id() -> int:
    return int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])


def _fixture(*, with_class: bool = True, with_group: bool = True) -> dict[str, str | int]:
    suffix = uuid4().hex[:10]
    center_id = f"legacy-adopt-center-{suffix}"
    class_id = f"legacy-adopt-class-{suffix}"
    group_id = f"legacy-adopt-group-{suffix}"
    now = _now()
    with transaction() as connection:
        for unit_id, unit_type, parent_id, name in (
            (center_id, "REGIONAL_CENTER", "org-suzhou", "历史承接测试中心"),
            (class_id, "CLASS", center_id, "历史承接测试班"),
            (group_id, "GROUP", class_id, "历史承接测试组"),
        ):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (unit_id, unit_id.upper(), name, unit_type, parent_id, now, now),
            )
        cursor = execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, class_name, group_name, "
            "class_committee_name, created_at, updated_at) VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?)",
            (
                f"LEGACY-ADOPT-{suffix}",
                "历史承接测试学员",
                center_id,
                "历史承接测试班" if with_class else None,
                "历史承接测试组" if with_group else None,
                None,
                now,
                now,
            ),
        )
        member_id = int(cursor.lastrowid)
        relations = [("PRIMARY_REGION", center_id)]
        if with_class:
            relations.append(("STUDY_CLASS", class_id))
        if with_group:
            relations.append(("STUDY_GROUP", group_id))
        for relation_type, org_unit_id in relations:
            execute(
                connection,
                "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (member_id, org_unit_id, relation_type, now, now),
            )
    return {
        "center_id": center_id,
        "class_id": class_id,
        "group_id": group_id,
        "member_id": member_id,
    }


def _set_legacy(member_id: int, value: str | None) -> None:
    with transaction() as connection:
        execute(
            connection,
            "UPDATE members SET class_committee_name=?, updated_at=? WHERE id=?",
            (value, _now(), member_id),
        )


def _item(preview: dict, member_id: int) -> dict:
    return next(
        row
        for row in preview["auto_adoptable_items"] + preview["manual_review_items"]
        if row["member_id"] == member_id
    )


def _apply(preview: dict, member_id: int) -> dict:
    return apply_legacy_volunteer_adoption(
        _admin_id(),
        preview_fingerprint=preview["preview_fingerprint"],
        member_ids=[member_id],
        confirmation=LEGACY_POSITION_AUTO_ADOPT_CONFIRMATION,
    )


@pytest.mark.parametrize(
    ("legacy_name", "position_key", "scope_level", "scope_field"),
    [
        ("辅导员", "volunteer_group_counselor", "GROUP", "group_id"),
        ("组长", "volunteer_group_leader", "GROUP", "group_id"),
        ("班长", "volunteer_class_monitor", "CLASS", "class_id"),
        ("副班主任", "volunteer_deputy_class_teacher", "CLASS", "class_id"),
        ("班主任", "volunteer_class_counselor", "CLASS", "class_id"),
    ],
    ids=["counselor", "group-leader", "class-monitor", "deputy-class-teacher", "class-counselor"],
)
def test_supported_legacy_positions_are_previewed_and_adopted(
    legacy_name: str,
    position_key: str,
    scope_level: str,
    scope_field: str,
) -> None:
    data = _fixture()
    _set_legacy(int(data["member_id"]), legacy_name)

    preview = preview_legacy_volunteer_adoption(_admin_id())
    row = _item(preview, int(data["member_id"]))
    assert row["auto_adoptable"] is True
    assert row["position_key"] == position_key
    assert row["scope_level"] == scope_level
    assert row["scope"]["scope_org_unit_id"] == data[scope_field]
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM volunteer_appointments va "
        "JOIN member_identities mi ON mi.person_id=va.person_id WHERE mi.member_id=?",
        (data["member_id"],),
    )["count"] == 0

    result = _apply(preview, int(data["member_id"]))
    assert result["adopted_count"] == 1
    assert result["skipped_count"] == 0
    appointment = fetch_one(
        "SELECT va.appointment_key, va.org_unit_id, va.scope_type, va.starts_at, va.ends_at, "
        "va.status, va.source_reference, mi.status AS identity_status "
        "FROM volunteer_appointments va JOIN member_identities mi ON mi.person_id=va.person_id "
        "WHERE mi.member_id=? AND va.status='ACTIVE'",
        (data["member_id"],),
    )
    assert appointment["appointment_key"] == position_key
    assert appointment["org_unit_id"] == data[scope_field]
    assert appointment["scope_type"] == "UNIT"
    assert appointment["starts_at"]
    assert appointment["ends_at"] is None
    assert appointment["status"] == "ACTIVE"
    assert appointment["source_reference"] == LEGACY_POSITION_AUTO_ADOPT_SOURCE
    assert appointment["identity_status"] == "ACTIVE"
    assert fetch_one(
        "SELECT class_committee_name FROM members WHERE id=?", (data["member_id"],)
    )["class_committee_name"] == legacy_name
    services = get_member_volunteer_services(int(data["member_id"]))
    assert services["is_volunteer"] is True
    assert services["roles"][0]["position_key"] == position_key
    assert services["roles"][0]["scope_org_unit_id"] == data[scope_field]
    assert "STUDY_MEETING_MANAGE" in services["roles"][0]["capabilities"]
    audit = fetch_one(
        "SELECT COUNT(*) AS count FROM audit_logs WHERE action=? AND resource_id=?",
        ("members.legacy_volunteer_position.auto_adopt", str(data["member_id"])),
    )
    assert audit["count"] == 1


@pytest.mark.parametrize(
    ("legacy_name", "position_key", "scope_level"),
    [
        ("理事志工", "volunteer_director", "REGIONAL_CENTER"),
        ("专项活动志工", "volunteer_activity", "ANY"),
    ],
    ids=["regional-center", "any-uses-current-center"],
)
def test_regional_and_any_history_use_the_current_center(
    legacy_name: str, position_key: str, scope_level: str
) -> None:
    data = _fixture()
    _set_legacy(int(data["member_id"]), legacy_name)

    row = _item(preview_legacy_volunteer_adoption(_admin_id()), int(data["member_id"]))
    assert row["auto_adoptable"] is True
    assert row["position_key"] == position_key
    assert row["scope_level"] == scope_level
    assert row["scope"]["scope_org_unit_id"] == data["center_id"]


def test_unmatched_or_multi_role_history_never_writes() -> None:
    unknown = _fixture()
    _set_legacy(int(unknown["member_id"]), "旧岗位-无法匹配")
    multi = _fixture()
    _set_legacy(int(multi["member_id"]), "辅导员、组委")

    preview = preview_legacy_volunteer_adoption(_admin_id())
    unknown_item = _item(preview, int(unknown["member_id"]))
    multi_item = _item(preview, int(multi["member_id"]))
    assert unknown_item["reason_code"] == "HISTORICAL_POSITION_UNKNOWN"
    assert multi_item["reason_code"] == "MULTIPLE_HISTORICAL_POSITIONS"
    for data in (unknown, multi):
        assert fetch_one(
            "SELECT COUNT(*) AS count FROM member_identities WHERE member_id=?",
            (data["member_id"],),
        )["count"] == 0


@pytest.mark.parametrize(
    "legacy_name",
    ["分中心服务志工", "班委", "组委"],
    ids=["regional-service-umbrella", "class-committee-umbrella", "group-committee-umbrella"],
)
def test_hidden_umbrella_history_requires_manual_review(legacy_name: str) -> None:
    data = _fixture()
    _set_legacy(int(data["member_id"]), legacy_name)

    row = _item(preview_legacy_volunteer_adoption(_admin_id()), int(data["member_id"]))
    assert row["reason_code"] == "HIDDEN_POSITION_REQUIRES_REVIEW"
    assert row["auto_adoptable"] is False
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM member_identities WHERE member_id=?",
        (data["member_id"],),
    )["count"] == 0


def test_group_role_without_formal_group_is_manual_review_only() -> None:
    data = _fixture(with_group=False)
    _set_legacy(int(data["member_id"]), "辅导员")

    preview = preview_legacy_volunteer_adoption(_admin_id())
    row = _item(preview, int(data["member_id"]))
    assert row["reason_code"] == "MISSING_FORMAL_GROUP"
    assert row["auto_adoptable"] is False
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM member_identities WHERE member_id=?",
        (data["member_id"],),
    )["count"] == 0


def test_multiple_formal_groups_require_manual_review() -> None:
    data = _fixture()
    second_group_id = f"legacy-adopt-second-group-{uuid4().hex[:10]}"
    now = _now()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, 'GROUP', ?, 1, ?, ?)",
            (
                second_group_id,
                second_group_id.upper(),
                "历史承接测试第二组",
                data["class_id"],
                now,
                now,
            ),
        )
        execute(
            connection,
            "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, created_at, updated_at) "
            "VALUES (?, ?, 'STUDY_GROUP', 0, ?, ?)",
            (data["member_id"], second_group_id, now, now),
        )
    _set_legacy(int(data["member_id"]), "辅导员")

    row = _item(preview_legacy_volunteer_adoption(_admin_id()), int(data["member_id"]))
    assert row["reason_code"] == "AMBIGUOUS_FORMAL_GROUP"
    assert row["auto_adoptable"] is False


def test_inactive_member_is_not_auto_adopted() -> None:
    data = _fixture()
    _set_legacy(int(data["member_id"]), "辅导员")
    with transaction() as connection:
        execute(
            connection,
            "UPDATE members SET status='INACTIVE', updated_at=? WHERE id=?",
            (_now(), data["member_id"]),
        )

    row = _item(preview_legacy_volunteer_adoption(_admin_id()), int(data["member_id"]))
    assert row["reason_code"] == "MEMBER_NOT_ACTIVE"
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM volunteer_appointments va "
        "JOIN member_identities mi ON mi.person_id=va.person_id WHERE mi.member_id=?",
        (data["member_id"],),
    )["count"] == 0


def test_existing_active_current_position_is_never_overwritten() -> None:
    data = _fixture()
    actor = _admin_id()
    set_member_current_volunteer_position(actor, int(data["member_id"]), "volunteer_group_leader")
    _set_legacy(int(data["member_id"]), "辅导员")

    row = _item(preview_legacy_volunteer_adoption(actor), int(data["member_id"]))
    assert row["reason_code"] == "ALREADY_CURRENT_POSITION"
    assert row["current_appointment"]["position_key"] == "volunteer_group_leader"
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM volunteer_appointments va "
        "JOIN member_identities mi ON mi.person_id=va.person_id "
        "WHERE mi.member_id=? AND va.status='ACTIVE'",
        (data["member_id"],),
    )["count"] == 1


def test_multiple_active_positions_are_not_collapsed() -> None:
    data = _fixture()
    actor = _admin_id()
    set_member_current_volunteer_position(actor, int(data["member_id"]), "volunteer_group_leader")
    _set_legacy(int(data["member_id"]), "辅导员")
    person_id = fetch_one(
        "SELECT person_id FROM member_identities WHERE member_id=?", (data["member_id"],)
    )["person_id"]
    now = datetime.now(UTC)
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO volunteer_appointments(person_id, appointment_key, org_unit_id, scope_type, "
            "starts_at, ends_at, status, source_reference, created_at, updated_at) "
            "VALUES (?, 'volunteer_activity', ?, 'UNIT', ?, NULL, 'ACTIVE', 'LEGACY_IMPORT', ?, ?)",
            (
                person_id,
                data["center_id"],
                (now - timedelta(days=1)).isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )

    row = _item(preview_legacy_volunteer_adoption(actor), int(data["member_id"]))
    assert row["reason_code"] == "MULTIPLE_ACTIVE_APPOINTMENTS"
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM volunteer_appointments va "
        "JOIN member_identities mi ON mi.person_id=va.person_id "
        "WHERE mi.member_id=? AND va.status='ACTIVE'",
        (data["member_id"],),
    )["count"] == 2


def test_adoption_audit_uses_confirmation_time_and_ordinary_edit_keeps_service() -> None:
    data = _fixture()
    actor = _admin_id()
    _set_legacy(int(data["member_id"]), "辅导员")
    preview = preview_legacy_volunteer_adoption(actor)
    _apply(preview, int(data["member_id"]))
    before = fetch_one(
        "SELECT va.id, va.starts_at, va.source_reference FROM volunteer_appointments va "
        "JOIN member_identities mi ON mi.person_id=va.person_id "
        "WHERE mi.member_id=? AND va.status='ACTIVE'",
        (data["member_id"],),
    )

    update_member(actor, int(data["member_id"]), {"company_name": "普通资料编辑不影响志工"})

    after = fetch_one(
        "SELECT va.id, va.starts_at, va.source_reference FROM volunteer_appointments va "
        "JOIN member_identities mi ON mi.person_id=va.person_id "
        "WHERE mi.member_id=? AND va.status='ACTIVE'",
        (data["member_id"],),
    )
    assert after == before
    assert get_member_volunteer_services(int(data["member_id"]))["is_volunteer"] is True
    assert "普通资料编辑不影响志工" == fetch_one(
        "SELECT company_name FROM members WHERE id=?", (data["member_id"],)
    )["company_name"]
    audit = fetch_one(
        "SELECT after_json FROM audit_logs WHERE action='members.legacy_volunteer_position.auto_adopt' "
        "AND resource_id=? ORDER BY id DESC LIMIT 1",
        (str(data["member_id"]),),
    )
    assert "SYSTEM_CONFIRMATION_TIME_ONLY" in audit["after_json"]


def test_repeating_the_same_apply_is_idempotent() -> None:
    data = _fixture()
    actor = _admin_id()
    _set_legacy(int(data["member_id"]), "组长")
    preview = preview_legacy_volunteer_adoption(actor)
    first = _apply(preview, int(data["member_id"]))
    second = _apply(preview, int(data["member_id"]))
    assert first["adopted_count"] == 1
    assert second["adopted_count"] == 0
    assert second["skipped_count"] == 1
    assert second["skipped"][0]["status"] == "IDEMPOTENT_SKIP"
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM volunteer_appointments va "
        "JOIN member_identities mi ON mi.person_id=va.person_id "
        "WHERE mi.member_id=? AND va.status='ACTIVE'",
        (data["member_id"],),
    )["count"] == 1


def test_preview_endpoint_is_read_only_and_apply_keeps_write_gate(monkeypatch) -> None:
    data = _fixture()
    _set_legacy(int(data["member_id"]), "辅导员")
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "test-admin-password"},
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        unauthenticated = client.get("/api/v1/volunteer-legacy-adoption/preview")
        assert unauthenticated.status_code == 401
        response = client.get(
            "/api/v1/volunteer-legacy-adoption/preview", headers=headers
        )
        assert response.status_code == 200, response.text
        preview = response.json()["data"]
        row = _item(preview, int(data["member_id"]))
        assert row["auto_adoptable"] is True
        assert preview["no_write"] is True
        assert fetch_one(
            "SELECT COUNT(*) AS count FROM member_identities WHERE member_id=?",
            (data["member_id"],),
        )["count"] == 0

        monkeypatch.setenv("IDENTITY_ADMIN_WRITES_ENABLED", "false")
        apply = client.post(
            "/api/v1/volunteer-legacy-adoption/apply",
            headers=headers,
            json={
                "preview_fingerprint": preview["preview_fingerprint"],
                "member_ids": [data["member_id"]],
                "confirmation": LEGACY_POSITION_AUTO_ADOPT_CONFIRMATION,
            },
        )
        assert apply.status_code == 403, apply.text
        assert fetch_one(
            "SELECT COUNT(*) AS count FROM member_identities WHERE member_id=?",
            (data["member_id"],),
        )["count"] == 0
