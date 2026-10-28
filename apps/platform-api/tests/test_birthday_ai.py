import httpx
import pytest
from app.services.birthday_ai import generate_body


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("BIRTHDAY_AI_ENABLED", "true")
    monkeypatch.setenv("CLOUDBASE_AI_ENV_ID", "synthetic-test-env")
    monkeypatch.setenv("CLOUDBASE_AI_API_KEY", "synthetic-key")
    monkeypatch.setenv("BIRTHDAY_AI_MODEL", "deepseek-v4-flash")


def test_disabled_makes_no_call(monkeypatch):
    monkeypatch.setenv("BIRTHDAY_AI_ENABLED", "false")
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: pytest.fail("unexpected provider call"))
    assert generate_body(years=3, participation=None, tone="warm")["status"] == "DISABLED"


def test_payload_anonymous_and_bounded(enabled, monkeypatch):
    calls = []
    body = "愿这份因学习结下的缘分，带来温暖与力量。祝您在新的一岁里身体安康、心有所悦，在工作之外也能享受生活的从容与欢喜。"
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request("POST", url), json={"choices":[{"message":{"content":body}}], "usage":{"total_tokens":120}})
    monkeypatch.setattr(httpx, "post", post)
    result = generate_body(years=7, participation="读书学习", tone="warm")
    assert result["body"] == body
    assert calls[0][1]["json"]["max_tokens"] == 480
    assert "长期同行" in calls[0][1]["json"]["messages"][1]["content"]
    assert not calls[0][1]["follow_redirects"]


def test_provider_failure_safe_fallback(enabled, monkeypatch):
    def fail(*a, **k):
        raise httpx.ConnectTimeout("must not expose provider data")
    monkeypatch.setattr(httpx, "post", fail)
    assert generate_body(years=None, participation=None, tone="warm") == {"status":"PROVIDER_UNAVAILABLE"}


def test_retired_model_not_called(enabled, monkeypatch):
    monkeypatch.setenv("BIRTHDAY_AI_MODEL", "hy3-preview")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("unexpected call"))
    assert generate_body(years=0, participation=None, tone="concise")["status"] == "CONFIGURATION_REQUIRED"
