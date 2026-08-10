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
            {"count": 22},
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
            {"key": "active_members_expected_no_study_group", "count": 22},
        ],
    }


def test_reconciliation_summary_scopes_counts_to_selected_activity_month():
    with patch("app.api.attendance.fetch_one", return_value={"count": 1}) as fetch_one_mock:
        _attendance_reconciliation_summary("2026-08")

    statements = [call.args[0] for call in fetch_one_mock.call_args_list]
    assert any("substr(eg.event_date, 1, 7)=?" in statement for statement in statements)
    assert all(call.args[1] == ("2026-08",) for call in fetch_one_mock.call_args_list)
