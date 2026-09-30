from __future__ import annotations

import os
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import execute, transaction
from app.main import app
from app.services.learning_credits import _insert_entry
from app.services.wechat_credit_summary import (
    MemberCreditEntryNotFound, get_member_credit_entries, get_member_credit_entry,
)
from test_v12_mvp import _seed_group_leader_fixture
from test_wechat_learning_summary import _bind


def entry(fixture, *, member_id=None, **overrides):
    key = str(uuid4())
    item = {
        "member_id": member_id or fixture["member_id"], "credit_category": "STANDARD_LEARNING",
        "credit_type": "COURSE_COMPLETION", "points": 2, "source_type": "GROUP_MEETING",
        "source_id": key, "class_org_unit_id": fixture["class_id"], "rule_key": "COURSE_COMPLETION",
        "rule_version": "generic-v1", "rule_snapshot": {
            "course_name": "经营十二条", "course_rule_version_label": "course-v2",
            "private_note": "DO_NOT_PUBLISH", "token": "DO_NOT_PUBLISH",
        }, "occurred_at": "2026-09-20T10:00:00+08:00", "idempotency_key": key,
    }
    status = overrides.pop("status", "POSTED")
    item.update(overrides)
    with transaction() as connection:
        return _insert_entry(connection, item, status=status, actor_user_id=None)


def test_history_snapshot_pagination_excludes_new_appends_and_foreign_entries():
    fixture = _seed_group_leader_fixture()
    own = [entry(fixture)["id"] for _ in range(25)]
    entry(fixture, member_id=fixture["foreign_member_id"])
    entry(fixture, status="PENDING")
    first = get_member_credit_entries(fixture["member_id"])
    assert len(first["entries"]) == 20 and first["has_more"]
    entry(fixture, occurred_at="2026-10-01T10:00:00+08:00")
    second = get_member_credit_entries(fixture["member_id"], offset=20, snapshot_id=int(first["snapshot_id"]))
    assert not second["has_more"] and second["next_offset"] == 25
    refs = [item["entry_ref"] for item in first["entries"] + second["entries"]]
    assert refs == [str(value) for value in reversed(own)]
    assert len(set(refs)) == 25
    assert set(first["entries"][0]) == {
        "entry_ref", "period_display", "credit_category_label", "credit_type_label",
        "rule_basis", "points", "is_reversal",
    }


def test_details_ownership_versions_privacy_and_reversal():
    fixture = _seed_group_leader_fixture()
    original = entry(fixture)
    reversal = entry(fixture, points=-2, source_type="REVERSAL", reversal_of_entry_id=original["id"])
    foreign = entry(fixture, member_id=fixture["foreign_member_id"])
    draft = entry(fixture, status="PENDING")
    for forbidden in [foreign["id"], draft["id"], 9223372036854775807]:
        with pytest.raises(MemberCreditEntryNotFound):
            get_member_credit_entry(fixture["member_id"], forbidden)
    detail = get_member_credit_entry(fixture["member_id"], original["id"])
    assert detail["title"] == "经营十二条"
    assert detail["rule_version"] == "generic-v1" and detail["course_rule_version"] == "course-v2"
    assert detail["is_reversed"] and detail["original_entry_ref"] is None
    negative = get_member_credit_entry(fixture["member_id"], reversal["id"])
    assert negative["points"] == "-2.00" and negative["is_reversal"]
    assert negative["original_entry_ref"] == str(original["id"])
    assert "DO_NOT_PUBLISH" not in str(detail)
    assert not {"source_id", "source_type", "member_id", "rule_snapshot_json"} & detail.keys()


@pytest.mark.parametrize("precision,month,display", [("YEAR", None, "2025年"), ("MONTH", 7, "2025年7月")])
def test_history_preserves_original_precision_and_value(precision, month, display):
    fixture = _seed_group_leader_fixture()
    row = entry(fixture, source_type="LEGACY_SUZHOU_2026_V1", credit_type="LEGACY_CLASS_MEETING",
                points=12.5, occurred_at=None, occurred_precision=precision, occurred_year=2025,
                occurred_month=month)
    detail = get_member_credit_entry(fixture["member_id"], row["id"])
    assert detail["period_display"] == display and detail["points"] == "12.50"
    assert "未重算" in detail["rule_basis"]


def test_unsafe_public_label_is_not_published():
    fixture = _seed_group_leader_fixture()
    row = entry(fixture, rule_snapshot={"course_name": "token PRIVATE", "course_rule_version_label": "secret"})
    detail = get_member_credit_entry(fixture["member_id"], row["id"])
    assert detail["title"] == "课程完成" and detail["course_rule_version"] == "课程规则版本待确认"


def test_http_session_scope_bounds_and_inactive_member():
    fixture = _seed_group_leader_fixture()
    own = entry(fixture)
    foreign = entry(fixture, member_id=fixture["foreign_member_id"])
    with patch.dict(os.environ, {
        "WECHAT_MEMBER_BINDING_ENABLED": "true", "WECHAT_MINIPROGRAM_APP_ID": "credit-history-test",
        "WECHAT_MINIPROGRAM_APP_SECRET": "synthetic",
    }), patch("app.services.wechat_identity.exchange_wechat_code", return_value={
        "appid": "credit-history-test", "openid": f"credit-history-{fixture['suffix']}",
    }), TestClient(app) as client:
        assert client.get("/api/v1/wechat/credit-entries").status_code == 401
        assert client.get(f"/api/v1/wechat/credit-entries/{own['id']}").status_code == 401
        headers = _bind(fixture, client)
        response = client.get(f"/api/v1/wechat/credit-entries?member_id={fixture['foreign_member_id']}", headers=headers)
        assert [row["entry_ref"] for row in response.json()["data"]["entries"]] == [str(own["id"])]
        assert client.get(f"/api/v1/wechat/credit-entries/{foreign['id']}", headers=headers).status_code == 404
        assert client.get(f"/api/v1/wechat/credit-entries/{own['id']}", headers=headers).status_code == 200
        for query in ["limit=0", "limit=51", "offset=-1", "offset=9223372036854775808", "snapshot_id=-1", "snapshot_id=9223372036854775808"]:
            assert client.get(f"/api/v1/wechat/credit-entries?{query}", headers=headers).status_code == 422
        assert client.get("/api/v1/wechat/credit-entries/9223372036854775808", headers=headers).status_code == 422
        with transaction() as connection:
            execute(connection, "UPDATE members SET status='INACTIVE' WHERE id=?", (fixture["member_id"],))
        assert client.get("/api/v1/wechat/credit-entries", headers=headers).status_code == 401
