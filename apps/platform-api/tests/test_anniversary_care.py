from datetime import date

import pytest

from app.services import anniversary_care, birthday_ai


def test_anniversary_counts_completed_years_and_handles_leap_day():
    assert anniversary_care._anniversary(date(2025, 11, 15), date(2026, 11, 15)) == 1
    assert anniversary_care._anniversary(date(2026, 11, 15), date(2026, 11, 15)) is None
    assert anniversary_care._anniversary(date(2025, 11, 15), date(2026, 11, 14)) is None
    assert anniversary_care._anniversary(date(2024, 2, 29), date(2025, 2, 28)) == 1
    assert anniversary_care._anniversary(date(2024, 2, 29), date(2028, 2, 28)) is None


@pytest.mark.parametrize(("body", "status"), [
    ("回望您曾参与【片段1】，这段学习时光为周年添上一份温暖。愿您和学长们珍惜同行的缘分。", "GENERATED"),
    ("回望您曾参与【片段1】，我们珍惜这段学习时光。祝您往后的日子从容而欢喜。", "OUTPUT_REJECTED"),
    ("感谢您的学习与同行，愿您和学长们珍惜这份缘分，往后的日子从容而欢喜。", "OUTPUT_REJECTED"),
])
def test_model_requires_real_memory_slots_and_respectful_pronouns(monkeypatch, body, status):
    monkeypatch.setenv("BIRTHDAY_AI_ENABLED", "true")
    monkeypatch.setenv("CLOUDBASE_AI_ENV_ID", "test-env")
    monkeypatch.setenv("CLOUDBASE_AI_API_KEY", "fixture-value")
    monkeypatch.setenv("BIRTHDAY_AI_MODEL", "hy3")

    def post(_url, **kwargs):
        assert "片段1" in kwargs["json"]["messages"][1]["content"]
        return birthday_ai.httpx.Response(
            200,
            request=birthday_ai.httpx.Request("POST", _url),
            json={"choices": [{"message": {"content": body}}]},
        )

    monkeypatch.setattr(birthday_ai.httpx, "post", post)
    assert birthday_ai.generate_body(
        years=4, participation="相聚共学", tone="warm", anniversary_memories=1
    )["status"] == status


def test_anniversary_draft_inserts_verified_memories_locally(monkeypatch):
    from contextlib import nullcontext

    monkeypatch.setattr(anniversary_care, "business_today", lambda: date(2026, 11, 15))
    monkeypatch.setattr(anniversary_care, "fetch_one", lambda *_: {
        "name": "示例", "org_unit_id": "org-1", "join_date": "2023-11-15",
    })
    monkeypatch.setattr(anniversary_care, "accessible_org_ids", lambda _: {"org-1"})
    monkeypatch.setattr(anniversary_care, "verified_member_memories", lambda *_, **__: [
        {"id": "1", "occurred_on": "2026-04-03", "title": "4月3日春季学习会", "activity_type": "CLASS_MEETING"},
        {"id": "2", "occurred_on": "2022-04-03", "title": "入塾前活动", "activity_type": "COURSE"},
    ])
    monkeypatch.setattr(anniversary_care, "transaction", lambda: nullcontext(None))
    monkeypatch.setattr(anniversary_care, "write_audit", lambda *_, **__: None)

    def generate(**kwargs):
        assert kwargs == {"years": 3, "participation": "相聚共学", "tone": "warm", "anniversary_memories": 1}
        return {"status": "GENERATED", "body": "回望您曾参与【片段1】。"}

    monkeypatch.setattr(anniversary_care, "generate_body", generate)
    actor = {"id": 1, "permissions": ["members:detail_view", "members:read"]}
    result = anniversary_care.generate_anniversary_draft(42, actor)
    assert "入塾3周年" in result["draft"]
    assert "4月3日春季学习会" in result["draft"]
    assert "2026-04-03" not in result["draft"]
    assert "入塾前活动" not in result["draft"]
    assert result["memories"][0]["period"] == "中期"

    monkeypatch.setattr(anniversary_care, "accessible_org_ids", lambda _: set())
    with pytest.raises(PermissionError):
        anniversary_care.generate_anniversary_draft(42, actor)


def test_review_chooses_one_event_per_period(monkeypatch):
    def memories(_member, *, limit, since, as_of):
        assert limit is None and since == date(2025, 11, 15) and as_of == date(2026, 11, 14)
        return [
            {"occurred_on": "2026-11-10"},
            {"occurred_on": "2026-11-09"},
            {"occurred_on": "2026-11-08"},
            {"occurred_on": "2026-05-02"},
            {"occurred_on": "2026-02-01"},
            {"occurred_on": "2025-10-10"},
        ]

    monkeypatch.setattr(anniversary_care, "verified_member_memories", memories)
    selected = anniversary_care._memories_by_period(42, date(2023, 11, 15), date(2026, 11, 15))
    assert [(item["period"], item["occurred_on"]) for item in selected] == [
        ("上期", "2026-02-01"), ("中期", "2026-05-02"), ("下期", "2026-11-10")
    ]


def test_mobile_greeting_without_verified_experience_does_not_invent_any():
    text = anniversary_care._care_greeting("示例", 7, [])
    assert "入塾7周年" in text
    assert "七载同行，感恩相遇" in text
    assert "回望" not in text
    assert "——苏州盛和塾" in text


def test_anniversary_review_and_send_are_shared_scoped_and_idempotent(monkeypatch):
    import sqlite3
    from contextlib import nullcontext
    from fastapi import HTTPException
    from app.api import members as web

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE members(id INTEGER, status TEXT, org_unit_id TEXT, name TEXT, join_date TEXT);
        INSERT INTO members VALUES (7, 'ACTIVE', 'class-1', '合成学员', '2019-11-23');
        CREATE TABLE audit_logs(id INTEGER PRIMARY KEY, actor_user_id INTEGER, action TEXT, resource_type TEXT,
            resource_id TEXT, org_unit_id TEXT, purpose TEXT, result TEXT, before_json TEXT,
            after_json TEXT, request_id TEXT, created_at TEXT);
        BEGIN IMMEDIATE;
    """)
    today = date(2026, 11, 23)
    monkeypatch.setattr(anniversary_care, "business_today", lambda: today)
    monkeypatch.setattr(anniversary_care, "transaction", lambda: nullcontext(conn))
    monkeypatch.setattr(anniversary_care, "accessible_org_ids", lambda _: {"class-1"})
    monkeypatch.setattr(anniversary_care, "resolve_member_scope", lambda _, org, allowed: org)
    monkeypatch.setattr(anniversary_care, "_member_contexts", lambda *_: {7: {"class_name": "合成班"}})
    monkeypatch.setattr(anniversary_care, "_care_memories", lambda *_: [
        {"id": "att-1", "occurred_on": "2026-03-08", "title": "春季学习会"},
    ])
    def call(action="view", selected=(), manage=True, read=True):
        return anniversary_care.process_anniversary_care(
            7, 8, due_date=today, may_read_memories=read,
            may_manage=manage, action=action, memory_ids=selected,
        )

    assert call()["selected_ids"] == ["att-1"]
    assert "春季学习会" in call()["draft"]
    assert "3月8日" not in call()["draft"]
    assert call()["memories"][0]["occurred_on"] == "2026-03-08"
    with pytest.raises(ValueError, match="先审核"):
        call("sent")
    with pytest.raises(PermissionError):
        call("review", ["att-1"], manage=False)
    with pytest.raises(ValueError, match="已核实"):
        call("review", ["other-member-memory"])
    assert call("preview", [])["selected_ids"] == []
    assert conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0
    assert call("review", ["att-1"])["review_status"] == "APPROVED"
    assert call("review", [])["selected_ids"] == ["att-1"]
    assert call(read=False)["draft"] == ""
    with pytest.raises(PermissionError):
        call("sent", read=False)
    actor = {"id": 9, "permissions": {"members:detail_view", "members:read", "followups:manage"}}
    scopes = {"members:detail_view": {"class-1"}, "members:read": set(), "followups:manage": set()}
    monkeypatch.setattr(web, "fetch_one", lambda *_: {"org_unit_id": "class-1"})
    monkeypatch.setattr(web, "can_access_member", lambda _, org, allowed: org in allowed)
    monkeypatch.setattr(web, "accessible_org_ids", lambda _, permission: scopes[permission])

    assert web.anniversary_workflow_detail(7, today, actor)["data"]["draft"] == ""
    with pytest.raises(HTTPException) as denied:
        web.anniversary_workflow_update(7, "sent", web.AnniversaryWorkflowPayload(due_date=today), actor)
    assert denied.value.status_code == 403
    scopes["members:read"].add("class-1")
    scopes["followups:manage"].add("class-1")
    assert web.anniversary_workflow_detail(7, today, actor)["data"]["draft"] == call()["draft"]
    sent = web.anniversary_workflow_update(7, "sent", web.AnniversaryWorkflowPayload(due_date=today), actor)
    assert sent["data"]["sent_date"] == today.isoformat()
    assert call("sent")["send_status"] == "SENT"
    assert conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 2
    conn.close()


def test_group_greeting_removes_dates_and_combines_same_day_sessions():
    memories = [
        {"occurred_on": "2026-09-28", "title": "928合成一班报告会空巴晚宴"},
        {"occurred_on": "2026-09-28", "title": "9月28日928合成一班报告会"},
        {"occurred_on": "2026-11-14", "title": "11月14–15日《示例课程》"},
    ]
    text = anniversary_care._care_greeting("合成学员", 2, memories)
    assert "合成一班报告会和《示例课程》" in text
    for fragment in ("9月28日", "928", "11月14", "空巴晚宴"):
        assert fragment not in text
    assert "空巴晚宴" in anniversary_care._care_greeting("合成学员", 2, memories[:1])
