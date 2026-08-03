from unittest.mock import patch

from app.api.attendance import _attendance_reconciliation_summary


def test_reconciliation_summary_returns_aggregate_counts_only():
    rows = iter(
        [
            {"count": 7},
            {"count": 3},
            {"count": 2},
            {"count": 18},
            {"count": 21},
        ]
    )
    with patch("app.api.attendance.fetch_one", side_effect=lambda _: next(rows)):
        result = _attendance_reconciliation_summary()

    assert result == {
        "scope": "AGGREGATE_ONLY",
        "write_enabled": False,
        "items": [
            {"key": "unmatched_attendance_records", "count": 7},
            {"key": "active_members_missing_phone_hash", "count": 3},
            {"key": "active_members_missing_primary_region", "count": 2},
            {"key": "active_members_missing_study_class", "count": 18},
            {"key": "active_members_missing_study_group", "count": 21},
        ],
    }
