from datetime import date
from unittest.mock import patch

import pytest

from app.services import wechat_operations as service
from app.services.renewals import determine_renewal_stage


@pytest.mark.parametrize("today", [date(2026, 10, 27), date(2026, 12, 31), date(2028, 2, 29)])
def test_mobile_watch_starts_with_current_month_and_crosses_years(today):
    rows = []
    for index, offset in enumerate((-1, 0, 1, 2, 3)):
        year, month = divmod(today.year * 12 + today.month - 1 + offset, 12)
        status = "PENDING_FIRST_CONTACT"
        rows.append({"id": index + 1, "member_id": index + 11, "renewal_year": year,
                     "due_month": month + 1, "status": status,
                     "stage": determine_renewal_stage(year, month + 1, status, as_of=today)})
    with patch.object(service, "_principal", return_value={}), patch.object(
        service, "_renewal_rows_for_principal", return_value=rows
    ):
        groups = service.renewal_watch({})["groups"]
    assert [g["key"] for g in groups] == ["FOLLOW_1", "RENEW_2", "OBSERVE_3"]
    assert [[item["cycle_id"] for item in g["items"]] for g in groups] == [[2], [3], [4]]
    assert groups[0]["items"][0]["status_label"] == "待首次联系"


@pytest.mark.parametrize("offset", [0, 1, 2])
def test_completed_renewals_are_hidden_in_every_mobile_watch_tab(offset):
    stage = {"months_until_due": offset}
    statuses = ["RENEWED", "PENDING_FIRST_CONTACT", "IN_COMMUNICATION", "NOT_RENEWING", "DEFERRED"]
    rows = [{"id": index + 1, "member_id": index + 11, "due_month": 10 + offset,
             "status": status, "stage": stage} for index, status in enumerate(statuses)]
    with patch.object(service, "_principal", return_value={}), patch.object(
        service, "_renewal_rows_for_principal", return_value=rows
    ):
        groups = service.renewal_watch({})["groups"]
    visible = [item for group in groups for item in group["items"]]
    assert [item["status"] for item in visible] == statuses[1:]
    assert rows[0]["status"] == "RENEWED", "Filtering must not modify the original renewal fact"
