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
        result = _attendance_sync_health()

    assert result["state"] == "HEALTHY"
    assert result["consecutive_failure_count"] == 0


def test_sync_health_reports_no_runs():
    with patch("app.api.attendance.fetch_all", return_value=[]):
        result = _attendance_sync_health()

    assert result["state"] == "NO_RUNS"
    assert result["last_run"] is None
