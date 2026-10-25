import copy
import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db import fetch_one, transaction, execute
from app.core.privacy import phone_hash
from app.main import app
from app.services import signin_engine
from test_v12_mvp import _seed_group_leader_fixture
from test_study_meeting_attendees import extra_member
from test_signin_management import ENV, _operator


def test_live_export_fills_formal_groups_and_deduplicates_daytime_attendees():
    f = _seed_group_leader_fixture()
    second = extra_member(f)
    events = {key: {"event_id": key, "event_group_id": "synthetic-meeting", "event_date": "2026-10-25", "activity_type": "CLASS_MEETING", "org_unit_id": f["class_id"], "class_org_unit_id": f["class_id"], "session_code": code, "lifecycle_status": "CONFIRMED"}
              for key, code in (("morning", "MORNING"), ("afternoon", "AFTERNOON"), ("konpa", "KONPA"))}
    phones = {}
    def row(member_id, key, **kwargs):
        member = fetch_one("SELECT id,member_code,name FROM members WHERE id=?", (member_id,))
        from uuid import uuid4
        phone = phones.setdefault(member_id, f"198{uuid4().int % 100_000_000:08d}")
        with transaction() as connection:
            execute(connection, "UPDATE members SET phone_hash=? WHERE id=?", (phone_hash(phone), member_id))
        return {"phone": phone, "registration_id": key, "platform_member_id": str(member["id"]), "member_code": member["member_code"], "name": member["name"], "group_name": "", "attendance_role": "HOME_CLASS_MEMBER", "checked": True, **kwargs}
    facts = {
        "morning": [row(f["member_id"], "one-am"), row(f["other_member_id"], "team", is_team=True), row(f["foreign_member_id"], "outside", attendance_role="CROSS_CLASS_MEMBER")],
        "afternoon": [row(f["member_id"], "one-pm"), row(second, "two-pm"), row(f["other_member_id"], "guest", attendance_role="GUEST")],
        "konpa": [row(f["other_member_id"], "night-only")],
    }
    calls = []
    legacy_mode = False
    def engine(path, envelope, **kwargs):
        operation = path.rsplit("/", 1)[-1]
        payload = envelope["payload"]
        calls.append((operation, payload))
        assert envelope["allowed_org_unit_ids"] == sorted([f["class_id"], f["group_id"], f["other_group_id"]])
        if operation == "event_detail":
            return {"ok": True, "event": copy.deepcopy(events[payload["event_id"]]), "sessions": copy.deepcopy(list(events.values())), "rows": copy.deepcopy(facts[payload["event_id"]])}
        if operation == "stats":
            return {"event": copy.deepcopy(events[payload["event_id"]]), "home_class_total": 35, "home_class_checked": 23, "cross_class_checked": 5, "guest_checked": 0, "onsite_total": 28, "rate": 65}
        if operation == "admin_events":
            return {"items": [{"event_group_id": "synthetic-meeting", "sessions": list(events.values())}], "has_more": False}
        assert operation == "export"
        event_id = payload["event_id"]
        result_rows = copy.deepcopy(facts[event_id])
        if legacy_mode:
            for exported in result_rows:
                member_id = exported.pop("platform_member_id", None)
                exported.pop("member_code", None)
                exported.pop("registration_id", None)
                exported["sign_time"] = "2026-10-25T01:00:00Z" if exported.pop("checked", False) else ""
        return {"ok": True, "event": copy.deepcopy(events[event_id]), "rows": result_rows}
    with patch.dict(os.environ, ENV), TestClient(app) as client, patch.object(signin_engine, "engine_request", side_effect=engine):
        headers, _ = _operator({"attendance:export"}, f["class_id"])
        result = client.post("/api/v1/attendance/manage/export", headers=headers, json={"event_id": "morning"})
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["stats"]["home_class_total"] == 35 and data["stats"]["onsite_total"] == 28
        assert data["rows"][0]["current_group_name"]
        groups = {group["group_org_unit_id"]: group for group in data["group_statistics"]}
        assert groups[f["group_id"]]["roster_count"] == 2
        assert groups[f["group_id"]]["present_count"] == 2
        assert groups[f["group_id"]]["participation_rate"] == 1
        assert groups[f["other_group_id"]]["present_count"] == 0
        assert groups[f["other_group_id"]]["participation_rate"] == 0
        assert data["rows"][0]["class_group_participation_rate"] == 1
        assert [payload["event_id"] for operation, payload in calls if operation == "export"] == ["morning"]
        assert data["class_statistics"]["present_count"] == 2
        assert data["class_statistics"]["roster_count"] == 3
        assert data["class_statistics"]["participation_rate"] == round(2 / 3, 6)
        view_headers, _ = _operator({"attendance:view"}, f["class_id"])
        calls.clear()
        viewed = client.post("/api/v1/attendance/manage/stats", headers=view_headers, json={"event_id": "morning"})
        assert viewed.status_code == 200, viewed.text
        assert viewed.json()["group_statistics"] == data["group_statistics"]
        assert viewed.json()["class_statistics"] == data["class_statistics"]
        assert all(operation != "export" for operation, _ in calls)
        assert "member_code" not in viewed.text

        # Actual legacy export contract omits all IDs and checked flags; only
        # a unique exact active name+phone can recover the formal identity.
        legacy_mode = True
        legacy = client.post("/api/v1/attendance/manage/export", headers=headers, json={"event_id": "morning"})
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()["rows"][0]["current_group_name"]
        assert legacy.json()["rows"][0]["class_group_participation_rate"] == 1
        assert all("phone" not in r for r in legacy.json()["rows"])
        legacy_mode = False
        # Class denominators retain members with no effective group.
        with transaction() as connection:
            execute(connection, "DELETE FROM member_org_relations WHERE member_id=? AND relation_type='STUDY_GROUP'", (f["other_member_id"],))
        ungrouped = client.post("/api/v1/attendance/manage/stats", headers=view_headers, json={"event_id": "morning"}).json()
        assert ungrouped["class_statistics"]["roster_count"] == 3
        assert ungrouped["group_totals"]["roster_count"] == 2
        assert ungrouped["group_totals"]["participation_rate"] == 1
        assert ungrouped["group_statistics"][-1]["group_name"] == "未分组"
        assert ungrouped["group_statistics"][-1]["roster_count"] == 1
        assert ungrouped["group_statistics"][-1]["present_count"] == 0
        # An unresolved home-class arrival must not turn into a plausible low rate.
        facts["morning"].append({"registration_id": "unknown", "name": "合成待核对人员", "checked": True, "attendance_role": "HOME_CLASS_MEMBER"})
        uncertain = client.post("/api/v1/attendance/manage/export", headers=headers, json={"event_id": "morning"})
        assert uncertain.status_code == 200
        assert "待核对" in uncertain.json()["group_statistics_message"]
        assert all(group["participation_rate"] is None for group in uncertain.json()["group_statistics"])


def test_view_statistics_has_its_own_scope_and_does_not_erase_session_totals_on_transient_failure():
    f = _seed_group_leader_fixture()
    event = {"event_id": "safe-event", "event_group_id": "safe-group", "event_date": "2026-10-25", "activity_type": "class_meeting", "class_org_unit_id": f["class_id"], "org_unit_id": f["class_id"]}
    def engine(path, payload, **kwargs):
        if path.endswith("/stats"):
            return {"event": copy.deepcopy(event), "total": 35, "checked": 23, "rate": 65.7}
        assert path.endswith("/event_detail")
        return {"event": copy.deepcopy(event), "rows": []}
    with patch.dict(os.environ, ENV), TestClient(app) as client, patch.object(signin_engine, "engine_request", side_effect=engine):
        outside, _ = _operator({"attendance:view"}, "org-wuxi")
        assert client.post("/api/v1/attendance/manage/stats", headers=outside, json={"event_id": "safe-event"}).status_code == 403
        headers, _ = _operator({"attendance:view"}, f["class_id"])
        def unavailable(path, payload, **kwargs):
            if path.endswith("/event_detail"):
                raise signin_engine.SigninEngineError("isolated temporary outage", 503)
            return engine(path, payload, **kwargs)
        with patch.object(signin_engine, "engine_request", side_effect=unavailable):
            result = client.post("/api/v1/attendance/manage/stats", headers=headers, json={"event_id": "safe-event"})
        assert result.status_code == 200, result.text
        assert result.json()["total"] == 35 and result.json()["checked"] == 23
        assert result.json()["class_statistics"] is None
        assert "暂不可用" in result.json()["group_statistics_message"]
