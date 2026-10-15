from types import SimpleNamespace

import httpx
import pytest

from app.services import attendance_registration_totals as totals
from app.api.attendance import _session_display_title


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    totals._cache.clear()
    monkeypatch.setattr(totals, "get_settings", lambda: SimpleNamespace(
        signin_api_base_url="https://signin.test", signin_service_api_key="isolated-test-key"))
    yield
    totals._cache.clear()


def client_for(monkeypatch, handler):
    factory = httpx.Client
    monkeypatch.setattr(totals.httpx, "Client", lambda **kwargs: factory(transport=httpx.MockTransport(handler), **kwargs))


def test_registration_total_includes_unchecked_leave_and_separate_team_slots(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.params["session_id"] == "approved-event"
        assert request.headers["X-API-Key"] == "isolated-test-key"
        return httpx.Response(200, json={"items": [
            {"external_record_id": f"registration-{i}", "name": "合成团队联系人",
             "attendance_status": "PRESENT" if i < 14 else "LEAVE" if i == 19 else "ABSENT"}
            for i in range(20)]})
    client_for(monkeypatch, handler)
    rows = [{"source_key": "signin", "external_session_id": "approved-event", "record_count": 14, "present_count": 14}]
    totals.attach_registration_counts(rows)
    assert rows[0]["registration_count"] == 20
    assert rows[0]["present_count"] == rows[0]["record_count"] == 14
    assert rows[0]["present_count"] / rows[0]["registration_count"] == .7
    totals.attach_registration_counts(rows)
    assert len(calls) == 1
    assert all(isinstance(value[1], int) for value in totals._cache.values())


def test_all_pages_required_and_duplicate_source_ids_do_not_inflate_total(monkeypatch):
    def handler(request):
        if request.url.params.get("cursor"):
            return httpx.Response(200, json={"items": [{"external_record_id": "a"}, {"external_record_id": "b"}]})
        return httpx.Response(200, json={"items": [{"external_record_id": "a"}], "next_cursor": "second"})
    client_for(monkeypatch, handler)
    rows = [{"source_key": "signin", "external_session_id": "approved-event", "record_count": 1}]
    totals.attach_registration_counts(rows)
    assert rows[0]["registration_count"] == 2


@pytest.mark.parametrize("failure", ["http", "cycle", "missing_id", "truncated"])
def test_partial_or_unavailable_roster_never_reuses_signed_count_as_denominator(monkeypatch, failure):
    def handler(request):
        if failure == "http":
            return httpx.Response(503)
        if failure == "missing_id":
            return httpx.Response(200, json={"items": [{}]})
        return httpx.Response(200, json={"items": [{"external_record_id": "a"}], "has_more": True,
                                        **({"next_cursor": "same"} if failure == "cycle" else {})})
    client_for(monkeypatch, handler)
    rows = [{"source_key": "signin", "external_session_id": "approved-event", "record_count": 14}]
    totals.attach_registration_counts(rows)
    assert rows[0]["registration_count"] is None
    assert rows[0]["registration_count_status"] == "UNAVAILABLE"


def test_local_imports_do_not_request_source_and_preserve_local_counts(monkeypatch):
    client_for(monkeypatch, lambda request: pytest.fail("unexpected source access"))
    rows = [{"source_key": "historical_import", "record_count": 20}]
    totals.attach_registration_counts(rows)
    assert rows[0]["registration_count"] == 20


@pytest.mark.parametrize("activity_type", ["OTHER", "other", "STAFF_TRAINING", "COURSE", "GROUP_MEETING"])
def test_only_class_meetings_have_morning_or_afternoon_title_suffix(activity_type):
    assert _session_display_title("新学员签到表 · 上午", "MORNING", "MORNING", activity_type) == "新学员签到表"
    assert _session_display_title("新学员签到表", "上午", "MORNING", activity_type) == "新学员签到表"
    assert _session_display_title("圆融五班班级学习会", "MORNING", "MORNING", "CLASS_MEETING") == "圆融五班班级学习会 · 上午"
