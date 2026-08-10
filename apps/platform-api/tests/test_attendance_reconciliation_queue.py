from unittest.mock import patch

from app.api.attendance import reconciliation_queue


def test_reconciliation_queue_is_paged_and_read_only():
    with patch(
        "app.api.attendance.fetch_one", return_value={"count": 276}
    ), patch(
        "app.api.attendance.fetch_all",
        return_value=[{"id": 9, "name_snapshot": "学员甲", "attendance_status": "UNMATCHED"}],
    ) as fetch_all_mock:
        result = reconciliation_queue(
            issue="unmatched_attendance_records", limit=20, offset=40, user={"id": 1}
        )

    assert result["data"]["scope"] == "MANUAL_REVIEW_READ_ONLY"
    assert result["data"]["issue"] == "unmatched_attendance_records"
    assert result["data"]["write_enabled"] is False
    assert result["data"]["total"] == 276
    assert result["data"]["rows"][0]["attendance_status"] == "UNMATCHED"
    assert fetch_all_mock.call_args.args[1] == (20, 40)


def test_reconciliation_queue_scopes_to_selected_month():
    with patch("app.api.attendance.fetch_one", return_value={"count": 3}), patch(
        "app.api.attendance.fetch_all", return_value=[]
    ) as fetch_all_mock:
        reconciliation_queue(
            issue="unmatched_attendance_records",
            month="2026-08",
            limit=20,
            offset=0,
            user={"id": 1},
        )

    assert "substr(eg.event_date, 1, 7)=?" in fetch_all_mock.call_args.args[0]
    assert fetch_all_mock.call_args.args[1] == ("2026-08", 20, 0)
