from copy import deepcopy

import pytest

from app.services.wechat_operations import _latest_event


@pytest.mark.parametrize("event_type", ["ATTENDANCE", "LEARNING_ACTIVITY"])
@pytest.mark.parametrize("session", ["上午", "下午", "晚上空巴", "空巴", "MORNING", "AFTERNOON", "KONPA"])
def test_mobile_latest_learning_uses_whole_activity_title(event_type, session):
    timeline = {"events": [
        {"event_type": "PROFILE_CHANGE", "title": "档案变更"},
        {"event_type": event_type, "title": f"合成一班班级学习会 - {session}",
         "occurred_at": "2026-11-06", "status": "PRESENT", "channel": session},
        {"event_type": event_type, "title": "更早的学习会", "occurred_at": "2026-10-01"},
    ]}
    original = deepcopy(timeline)
    latest = _latest_event(timeline, {"ATTENDANCE", "LEARNING_ACTIVITY"})
    assert latest["title"] == "合成一班班级学习会"
    assert latest["occurred_at"] == "2026-11-06"
    assert latest["status"] == "PRESENT"
    assert timeline == original


def test_mobile_care_title_and_empty_learning_are_unchanged():
    care = {"events": [{"event_type": "FOLLOWUP_RECORD", "title": "电话关爱 - 下午"}]}
    assert _latest_event(care, {"FOLLOWUP_RECORD"})["title"] == "电话关爱 - 下午"
    assert _latest_event(care, {"ATTENDANCE", "LEARNING_ACTIVITY"}) is None
    assert _latest_event(None, {"ATTENDANCE"}) is None
