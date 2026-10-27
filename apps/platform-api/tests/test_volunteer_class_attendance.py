import copy
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db import execute, fetch_one, transaction
from app.main import app
from app.services import signin_engine, volunteer_class_attendance as service
from app.services.volunteer_positions import create_member_volunteer_appointment, get_member_volunteer_services, set_member_current_volunteer_position
from test_current_volunteer_service import _fixture, _admin_id
from test_study_meeting_attendees import extra_member
from test_signin_management import ENV

PREFIX = "/api/v1/wechat/volunteer-class-meetings"
ATTENDANCE_POSITIONS = ["volunteer_class_counselor", "volunteer_deputy_class_teacher",
    "volunteer_class_monitor", "volunteer_group_counselor", "volunteer_group_leader"]


def appointment(f, position="volunteer_class_counselor"):
    return set_member_current_volunteer_position(_admin_id(), f["member_id"], position)


@pytest.mark.parametrize("position", ATTENDANCE_POSITIONS)
def test_class_team_get_live_class_scope_without_backend_accounts(position):
    f = _fixture()
    appointment(f, position)
    roles = get_member_volunteer_services(f["member_id"])
    assert "CLASS_ATTENDANCE_VIEW" in roles["roles"][0]["capabilities"]
    assert service.context(f["member_id"])["classes"] == [{"id": f["class_id"], "name": "当前志工测试班"}]
    with transaction() as connection:
        execute(connection, "UPDATE volunteer_appointments SET status='ENDED' WHERE member_id=?", (f["member_id"],))
    with pytest.raises(PermissionError):
        service.context(f["member_id"])


@pytest.mark.parametrize("position", [None, "volunteer_regional_service"])
def test_other_volunteers_and_ordinary_members_cannot_read_class_statistics(position):
    f = _fixture()
    if position:
        appointment(f, position)
    with patch.object(signin_engine, "engine_request") as upstream:
        with pytest.raises(PermissionError):
            service.snapshot(f["member_id"], "any-event")
        upstream.assert_not_called()


@pytest.mark.parametrize("position", ATTENDANCE_POSITIONS)
def test_inactive_member_class_and_position_fail_closed(position):
    f = _fixture()
    appointment(f, position)
    with transaction() as connection:
        execute(connection, "UPDATE org_units SET is_active=0 WHERE id=?", (f["class_id"],))
    with pytest.raises(PermissionError):
        service.context(f["member_id"])
    with transaction() as connection:
        execute(connection, "UPDATE org_units SET is_active=1 WHERE id=?", (f["class_id"],))
        execute(connection, "UPDATE members SET status='INACTIVE' WHERE id=?", (f["member_id"],))
    with pytest.raises(PermissionError):
        service.context(f["member_id"])


@pytest.mark.parametrize("position", ["volunteer_group_counselor", "volunteer_group_leader"])
def test_group_post_uses_service_group_parent_and_rejects_inactive_or_invalid_scope(position):
    from app.services.volunteer_management import create_service_unit, create_appointment
    from uuid import uuid4
    f = _fixture()
    with transaction() as connection:
        execute(connection, "UPDATE org_units SET parent_id=? WHERE id=?", (f["other_class_id"], f["other_group_id"]))
    unit = create_service_unit(_admin_id(), unit_code="ATTENDANCE-GROUP-" + uuid4().hex[:10], name="合成跨班小组志工组",
        system_type="CLASS_TEAM", line_type="GENERAL", parent_id=None,
        home_shuku_org_unit_id="org-suzhou", service_target_org_unit_id=f["other_group_id"])
    create_appointment(_admin_id(), member_id=f["member_id"], position_key=position, service_unit_id=unit["id"], confirmation_note="合成小组服务范围验证")
    assert [r["id"] for r in service.context(f["member_id"])["classes"]] == [f["other_class_id"]]
    # A member's own study class must not provide extra access.
    with patch.object(signin_engine, "engine_request") as upstream:
        with pytest.raises(PermissionError):
            service.meetings(f["member_id"], month="2026-10", class_id=f["class_id"])
        upstream.assert_not_called()
    with transaction() as connection:
        execute(connection, "UPDATE org_units SET is_active=0 WHERE id=?", (f["other_group_id"],))
    with pytest.raises(PermissionError):
        service.context(f["member_id"])
    with transaction() as connection:
        execute(connection, "UPDATE org_units SET is_active=1,parent_id=? WHERE id=?", (f["center_id"], f["other_group_id"]))
    with pytest.raises(PermissionError):
        service.context(f["member_id"])


def test_v2_teacher_reads_service_class_instead_of_own_study_class():
    from app.services.volunteer_management import create_service_unit, create_appointment
    from uuid import uuid4
    f = _fixture()
    unit = create_service_unit(_admin_id(), unit_code="ATTENDANCE-" + uuid4().hex[:10], name="合成班级志工组",
        system_type="CLASS_TEAM", line_type="GENERAL", parent_id=None,
        home_shuku_org_unit_id="org-suzhou", service_target_org_unit_id=f["other_class_id"])
    create_appointment(_admin_id(), member_id=f["member_id"], position_key="volunteer_class_counselor", service_unit_id=unit["id"], confirmation_note="合成任职范围验证")
    assert [r["id"] for r in service.context(f["member_id"])["classes"]] == [f["other_class_id"]]


@pytest.mark.parametrize("position", ATTENDANCE_POSITIONS)
def test_read_only_api_shares_real_daytime_dedup_and_independent_scene_rules(position):
    f = _fixture()
    appointment(f, position)
    second = extra_member(f)
    third = extra_member(f, group=f["other_group_id"])
    events = {key: {"event_id": key, "event_group_id": "synthetic-class-day", "name": "合成班会", "event_date": "2026-10-27",
        "activity_type": "class_meeting", "org_unit_id": f["class_id"], "class_org_unit_id": f["class_id"],
        "session_code": code, "session_name": label, "lifecycle_status": "CONFIRMED"}
        for key, code, label in [("morning", "MORNING", "上午"), ("afternoon", "AFTERNOON", "下午"), ("konpa", "KONPA", "空巴")]}
    def row(mid):
        m = fetch_one("SELECT id,name,member_code FROM members WHERE id=?", (mid,))
        return {"registration_id": str(mid), "platform_member_id": str(mid), "member_code": m["member_code"], "name": m["name"],
                "phone": "SYNTHETIC-PRIVATE-CONTACT", "attendance_note": "SYNTHETIC-PRIVATE-NOTE", "checked": True, "attendance_role": "HOME_CLASS_MEMBER"}
    facts = {"morning": [row(f["member_id"]), row(second)], "afternoon": [row(second)], "konpa": [row(third)]}
    calls = []
    moved = [False]
    def engine(path, envelope, **kwargs):
        op = path.rsplit("/", 1)[-1]; payload = envelope["payload"]; calls.append((op, payload))
        assert op in {"admin_events", "event_detail", "stats"}
        assert envelope["allowed_org_unit_ids"] == [f["class_id"]]
        assert envelope["actor"]["permissions"] == ["attendance:view"]
        if op == "admin_events":
            return {"items": [{**events["morning"], "sessions": list(events.values())}], "has_more": False}
        if op == "stats":
            source = copy.deepcopy(events[payload["event_id"]])
            if moved[0]:
                source["event_group_id"] = "different-meeting-after-edit"
            return {"event": source, "onsite_total": len(facts[payload["event_id"]])}
        return {"event": copy.deepcopy(events[payload["event_id"]]), "sessions": copy.deepcopy(list(events.values())), "rows": copy.deepcopy(facts[payload["event_id"]])}
    with patch.dict("os.environ", ENV), TestClient(app) as client, patch.object(signin_engine, "engine_request", side_effect=engine), patch(
        "app.api.wechat.resolve_member_session", return_value={"member_id": f["member_id"]}
    ):
        headers = {"Authorization": "Bearer synthetic-volunteer-session"}
        assert client.get(PREFIX + "/context").status_code == 401
        context = client.get(PREFIX + "/context", headers=headers)
        assert context.status_code == 200, context.text
        listing = client.get(PREFIX, params={"month": "2026-10", "day": "2026-10-27", "class_org_unit_id": f["class_id"]}, headers=headers)
        assert listing.status_code == 200 and len(listing.json()["data"]["records"]) == 1
        assert calls[-1][1]["date_from"] == "2026-10-27" and calls[-1][1]["date_to"] == "2026-10-27"
        monthly = client.get(PREFIX, params={"month": "2026-10", "class_org_unit_id": f["class_id"]}, headers=headers)
        assert monthly.status_code == 200
        assert calls[-1][1]["date_from"] == "2026-10-01" and calls[-1][1]["date_to"] == "2026-10-31"
        detail = client.get(PREFIX + "/morning", headers=headers)
        assert detail.status_code == 200, detail.text
        scenes = {row["key"]: row for row in detail.json()["data"]["scenes"]}
        assert list(scenes) == ["overall", "morning", "afternoon", "konpa"]
        assert scenes["overall"]["class_statistics"]["present_count"] == 2
        assert scenes["morning"]["class_statistics"]["present_count"] == 2
        assert scenes["afternoon"]["class_statistics"]["present_count"] == 1
        assert scenes["konpa"]["class_statistics"]["present_count"] == 1
        assert scenes["overall"]["class_statistics"]["participation_rate"] == round(2 / 3, 6)
        assert next(g for g in scenes["afternoon"]["group_statistics"] if g["group_org_unit_id"] == f["group_id"])["present_count"] == 1
        assert next(g for g in scenes["konpa"]["group_statistics"] if g["group_org_unit_id"] == f["group_id"])["present_count"] == 0
        assert "SYNTHETIC-PRIVATE" not in detail.text and '"attendees"' not in detail.text and '"member_code"' not in detail.text
        facts["morning"].append({"registration_id": "synthetic-unresolved", "name": "合成待核对学长", "checked": True, "attendance_role": "HOME_CLASS_MEMBER"})
        uncertain = client.get(PREFIX + "/morning", headers=headers)
        assert uncertain.status_code == 200, uncertain.text
        per_scene = {row["key"]: row for row in uncertain.json()["data"]["scenes"]}
        assert per_scene["overall"]["class_statistics"]["participation_rate"] is None
        assert per_scene["morning"]["message"] and per_scene["morning"]["class_statistics"]["participation_rate"] is None
        assert not per_scene["afternoon"]["message"] and per_scene["afternoon"]["class_statistics"]["participation_rate"] == round(1 / 3, 6)
        facts["morning"].pop()
        moved[0] = True
        assert client.get(PREFIX + "/morning", headers=headers).status_code == 409
        moved[0] = False
        count = len(calls)
        outside = client.get(PREFIX, params={"month": "2026-10", "class_org_unit_id": f["other_class_id"]}, headers=headers)
        assert outside.status_code == 403 and len(calls) == count
        events["morning"]["class_org_unit_id"] = f["other_class_id"]
        assert client.get(PREFIX + "/morning", headers=headers).status_code == 403
        assert client.post(PREFIX + "/morning", json={}, headers=headers).status_code == 405
        with transaction() as connection:
            execute(connection, "UPDATE volunteer_appointments SET status='REVOKED' WHERE member_id=?", (f["member_id"],))
        count = len(calls)
        assert client.get(PREFIX + "/morning", headers=headers).status_code == 403 and len(calls) == count


def test_bad_dates_and_upstream_outage_never_return_plausible_zero_rates():
    f = _fixture(); appointment(f)
    with patch.dict("os.environ", ENV), patch.object(signin_engine, "engine_request", side_effect=signin_engine.SigninEngineError("签到暂不可用", 502)):
        with pytest.raises(ValueError):
            service.meetings(f["member_id"], month="2026-02", day="2026-02-30")
        with pytest.raises(signin_engine.SigninEngineError):
            service.snapshot(f["member_id"], "morning")
