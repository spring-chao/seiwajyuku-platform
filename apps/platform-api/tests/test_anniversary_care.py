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
        {"id": "1", "occurred_on": "2025-04-03", "title": "春季学习会", "activity_type": "CLASS_MEETING"},
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
    assert "2025-04-03的春季学习会" in result["draft"]
    assert "入塾前活动" not in result["draft"]

    monkeypatch.setattr(anniversary_care, "accessible_org_ids", lambda _: set())
    with pytest.raises(PermissionError):
        anniversary_care.generate_anniversary_draft(42, actor)
