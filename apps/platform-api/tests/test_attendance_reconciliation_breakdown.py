from unittest.mock import patch

from app.api.attendance import reconciliation_breakdown


def test_reconciliation_breakdown_is_aggregate_only():
    grouped = [{"org_unit_id": "sz", "org_name": "苏州运营中心", "count": 12}]
    with patch("app.api.attendance.fetch_all", return_value=grouped) as fetch_all_mock:
        result = reconciliation_breakdown(
            issue="active_members_missing_study_class", user={"id": 1}
        )

    assert result == {
        "success": True,
        "data": {
            "scope": "AGGREGATE_ONLY",
            "issue": "active_members_missing_study_class",
            "rows": grouped,
        },
    }
    assert "COUNT(*)" in fetch_all_mock.call_args.args[0]
