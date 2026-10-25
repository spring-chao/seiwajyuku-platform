import copy
import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db import fetch_one
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
    def row(member_id, key, **kwargs):
        member = fetch_one("SELECT id,member_code,name FROM members WHERE id=?", (member_id,))
        return {"registration_id": key, "platform_member_id": str(member["id"]), "member_code": member["member_code"], "name": member["name"], "group_name": "", "attendance_role": "HOME_CLASS_MEMBER", "checked": True, **kwargs}
    facts = {
        "morning": [row(f["member_id"], "one-am"), row(f["other_member_id"], "team", is_team=True), row(f["foreign_member_id"], "outside", attendance_role="CROSS_CLASS_MEMBER")],
        "afternoon": [row(f["member_id"], "one-pm"), row(second, "two-pm"), row(f["other_member_id"], "guest", attendance_role="GUEST")],
        "konpa": [row(f["other_member_id"], "night-only")],
    }
    calls = []
    def engine(path, envelope, **kwargs):
        operation = path.rsplit("/", 1)[-1]
        payload = envelope["payload"]
        calls.append((operation, payload))
        assert envelope["allowed_org_unit_ids"] == sorted([f["class_id"], f["group_id"], f["other_group_id"]])
        if operation == "event_detail":
            return {"ok": True, "event": events[payload["event_id"]]}
        if operation == "stats":
            return {"home_class_total": 35, "home_class_checked": 23, "cross_class_checked": 5, "guest_checked": 0, "onsite_total": 28, "rate": 65}
        if operation == "admin_events":
            return {"items": [{"event_group_id": "synthetic-meeting", "sessions": list(events.values())}], "has_more": False}
        assert operation == "export"
        event_id = payload["event_id"]
        return {"ok": True, "event": copy.deepcopy(events[event_id]), "rows": copy.deepcopy(facts[event_id])}
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
        assert [payload["event_id"] for operation, payload in calls if operation == "export"] == ["morning", "afternoon"]
        # An unresolved home-class arrival must not turn into a plausible low rate.
        facts["morning"].append({"registration_id": "unknown", "name": "合成待核对人员", "checked": True, "attendance_role": "HOME_CLASS_MEMBER"})
        uncertain = client.post("/api/v1/attendance/manage/export", headers=headers, json={"event_id": "morning"})
        assert uncertain.status_code == 200
        assert "待核对" in uncertain.json()["group_statistics_message"]
        assert all(group["participation_rate"] is None for group in uncertain.json()["group_statistics"])
