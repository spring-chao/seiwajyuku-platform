from datetime import UTC, datetime
from unittest.mock import patch

from app.api.attendance import _attendance_sync_health


def _run(run_id: int, status: str, *, error_summary: str | None = None) -> dict:
    return {
        "id": run_id,
        "status": status,
        "started_at": "2026-07-30T00:00:00+00:00",
        "finished_at": "2026-07-30T00:01:00+00:00",
        "received_sessions": 2,
        "received_records": 10,
        "error_count": 1 if status != "SUCCESS" else 0,
        "error_summary": error_summary,
    }


def test_sync_health_is_critical_after_three_consecutive_failures():
    rows = [
        _run(4, "ERROR", error_summary="private upstream detail"),
        _run(3, "PARTIAL", error_summary="private row detail"),
        _run(2, "ERROR", error_summary="private network detail"),
        _run(1, "SUCCESS"),
    ]
    with patch("app.api.attendance.fetch_all", return_value=rows):
        result = _attendance_sync_health()

    assert result["state"] == "CRITICAL"
    assert result["consecutive_failure_count"] == 3
    assert result["last_run"]["has_error_summary"] is True
    assert "error_summary" not in result["last_run"]
    assert "private" not in str(result)


def test_sync_health_resets_failure_count_after_success():
    rows = [
        _run(3, "SUCCESS"),
        _run(2, "ERROR", error_summary="old detail"),
        _run(1, "ERROR", error_summary="older detail"),
    ]
    with patch("app.api.attendance.fetch_all", return_value=rows):
        result = _attendance_sync_health(
            now=datetime(2026, 7, 30, 2, tzinfo=UTC)
        )

    assert result["state"] == "HEALTHY"
    assert result["consecutive_failure_count"] == 0


def test_sync_health_reports_no_runs():
    with patch("app.api.attendance.fetch_all", return_value=[]):
        result = _attendance_sync_health()

    assert result["state"] == "NO_RUNS"
    assert result["last_run"] is None


def test_sync_health_marks_an_old_success_as_stale_after_monday_grace_period():
    rows = [
        {
            **_run(1, "SUCCESS"),
            "started_at": "2026-08-13T16:00:13+00:00",
            "finished_at": "2026-08-14T00:00:34+08:00",
        }
    ]
    with patch("app.api.attendance.fetch_all", return_value=rows):
        result = _attendance_sync_health(
            now=datetime(2026, 8, 17, 0, tzinfo=UTC)
        )

    assert result["state"] == "STALE"
    assert result["expected_run_at"] == "2026-08-16T16:00:00+00:00"
    assert result["schedule_timezone"] == "Asia/Shanghai"
    assert result["grace_period_hours"] == 6


def test_sync_health_does_not_mark_friday_success_stale_during_weekend():
    rows = [
        {
            **_run(1, "SUCCESS"),
            "started_at": "2026-09-17T16:00:00+00:00",
        }
    ]
    with patch("app.api.attendance.fetch_all", return_value=rows):
        result = _attendance_sync_health(
            now=datetime(2026, 9, 20, 15, tzinfo=UTC)
        )

    assert result["state"] == "HEALTHY"
    assert result["expected_run_at"] == "2026-09-17T16:00:00+00:00"


def test_sync_health_allows_current_weekday_run_until_six_am_local():
    rows = [
        {
            **_run(1, "SUCCESS"),
            "started_at": "2026-09-17T16:00:00+00:00",
        }
    ]
    with patch("app.api.attendance.fetch_all", return_value=rows):
        before_grace = _attendance_sync_health(
            now=datetime(2026, 9, 20, 21, 59, tzinfo=UTC)
        )
        after_grace = _attendance_sync_health(
            now=datetime(2026, 9, 20, 22, 1, tzinfo=UTC)
        )

    assert before_grace["state"] == "HEALTHY"
    assert after_grace["state"] == "STALE"
