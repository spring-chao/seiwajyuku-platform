from datetime import date

import pytest

from app.services.care_copy import renewal_care_references
from app.services.member_memories import _memory_candidate


def _references(**overrides):
    args = dict(
        name="测试学姐", stage_code="DUE_NOW", status="IN_COMMUNICATION",
        renewal_year=2026, due_month=10, memories=[], latest=None,
        as_of=date(2026, 10, 15),
    )
    return renewal_care_references(**(args | overrides))


@pytest.mark.parametrize(("stage", "phrase"), [
    ("PREPARE", "最近怎么样"), ("OBSERVE_3", "最近怎么样"),
    ("RENEW_2", "将在10月到期"), ("FOLLOW_1", "是否继续学习"),
    ("DUE_NOW", "这个月是您的续费月份"), ("RECOVERY", "我们尊重您的安排"),
])
def test_copy_distinguishes_each_stage_and_does_not_invent_previous_contact(stage, phrase):
    wechat, phone = _references(stage_code=stage)
    assert phrase in wechat
    assert wechat.startswith("测试学姐好")
    assert "学姐学长" not in wechat
    assert "上次" not in wechat + phone
    if stage in {"PREPARE", "OBSERVE_3"}:
        assert "续费" not in wechat + phone


@pytest.mark.parametrize(("status", "latest", "phrase"), [
    ("IN_COMMUNICATION", {"needs_support": True}, "最希望我们协助的事"),
    ("DEFERRED", None, "按自己的节奏"),
    ("CONTACTED_WAITING_REPLY", None, "方便时再回"),
    ("IN_COMMUNICATION", {"next_followup_at": "2026-10-16T01:00:00+08:00"}, "不用急着回复"),
])
def test_contact_context_takes_priority_over_renewal_pressure(status, latest, phrase):
    wechat, phone = _references(status=status, latest=latest)
    assert phrase in wechat
    assert "续费月份" not in wechat
    assert "上次您说" not in wechat + phone


def test_unstructured_internal_notes_are_never_repeated_or_interpreted_as_commitments():
    wechat, phone = _references(latest={
        "summary": "内部情况不要外发", "intention": "已承诺一定续费",
        "next_action": "未经核验的优惠与代付承诺",
    })
    assert all(word not in wechat + phone for word in ("内部情况", "已承诺", "优惠", "代付"))


def test_support_wins_over_other_open_states_but_closed_cycle_never_reopens():
    assert "最希望我们协助" in _references(status="DEFERRED", latest={"needs_support": True})[0]
    assert _references(stage_code="CLOSED", status="RENEWED", latest={"needs_support": True}) == (None, None)


def test_newest_recent_experience_wins_over_old_special_event_and_is_not_repeated():
    memories = [
        dict(occurred_on="2025-07-03", year=2025, month=7, title="旧报告会"),
        dict(occurred_on="2026-09-14", year=2026, month=9, title="九月班会"),
        dict(occurred_on="2027-01-01", year=2027, month=1, title="未来课程"),
    ]
    wechat, _ = _references(stage_code="OBSERVE_3", memories=memories)
    assert "2026年9月的九月班会" in wechat
    assert "旧报告会" not in wechat
    assert "未来课程" not in wechat
    followup, _ = _references(stage_code="OBSERVE_3", memories=memories, latest={"id": 1})
    assert "班会" not in followup
    fallback, _ = _references(stage_code="OBSERVE_3", memories=memories[:1])
    assert "旧报告会" not in fallback
    assert "前段时间" not in fallback


def test_future_participation_is_not_a_verified_memory_yet():
    assert _memory_candidate(
        memory_id="future", occurred_on="2026-10-16", activity_type="COURSE",
        title="未来课程", source_type="ATTENDANCE", evidence_status="PRESENT",
        as_of=date(2026, 10, 15),
    ) is None


def test_future_followup_uses_shanghai_day_and_expired_plan_does_not_suppress_renewal():
    assert "不用急着回复" in _references(latest={"next_followup_at": "2026-10-15T17:00:00Z"})[0]
    assert "续费月份" in _references(latest={"next_followup_at": "2026-10-14"})[0]
    assert "2027年1月" in _references(stage_code="RENEW_2", renewal_year=2027, due_month=1)[0]
