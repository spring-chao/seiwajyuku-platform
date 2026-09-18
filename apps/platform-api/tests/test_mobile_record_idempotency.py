from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from unittest.mock import patch

import pytest

from app.db import atomic_transaction, execute, fetch_one, transaction
from app.services import wechat_operations as mobile
from test_staff_mobile_operations_v1 import _seed_staff_mobile_v1, _wechat_context


def _context(fixture):
    return {"person_id": fixture["person_id"], "verified_user_id": fixture["user_id"]}


def test_concurrent_mobile_retries_create_one_record_and_receipt():
    fixture = _seed_staff_mobile_v1()
    payload = dict(member_id=fixture["focal_member_id"], channel="WECHAT", situation="synthetic care",
                   next_action=None, next_followup_at=None, idempotency_key=uuid4().hex)
    with _wechat_context(), ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: mobile.record_care(_context(fixture), **payload), range(2)))
        assert results[0]["record_id"] == results[1]["record_id"]
        assert fetch_one("SELECT COUNT(*) AS n FROM followup_records WHERE member_id=?", (fixture["focal_member_id"],))["n"] == 1
        with pytest.raises(ValueError, match="内容已变化"):
            mobile.record_care(_context(fixture), **{**payload, "situation": "different"})
        with transaction() as connection:
            execute(connection, "UPDATE employee_authorization_grants SET status='REVOKED' WHERE employment_id=?", (fixture["employment_id"],))
        with pytest.raises(PermissionError):
            mobile.record_care(_context(fixture), **payload)


def test_renewal_retries_create_one_record():
    fixture = _seed_staff_mobile_v1()
    payload = dict(member_id=fixture["focal_member_id"], renewal_cycle_id=fixture["renewal_cycles"]["FOLLOW_1"],
                   channel="WECHAT", situation="synthetic renewal", next_action=None,
                   next_followup_at=None, idempotency_key=uuid4().hex)
    with _wechat_context():
        first = mobile.record_renewal_care(_context(fixture), **payload)
        second = mobile.record_renewal_care(_context(fixture), **payload)
        assert first["record_id"] == second["record_id"]
        assert second["replayed"]


def test_failure_after_record_write_rolls_back_fact_and_allows_safe_retry():
    fixture = _seed_staff_mobile_v1()
    payload = dict(member_id=fixture["focal_member_id"], channel="WECHAT", situation="synthetic care",
                   next_action=None, next_followup_at=None, idempotency_key=uuid4().hex)
    with _wechat_context():
        with patch.object(mobile, "_today_actions_for_principal", side_effect=RuntimeError("projection failed")):
            with pytest.raises(RuntimeError):
                mobile.record_care(_context(fixture), **payload)
        assert fetch_one("SELECT COUNT(*) AS n FROM followup_records WHERE member_id=?", (fixture["focal_member_id"],))["n"] == 0
        assert mobile.record_care(_context(fixture), **payload)["record_id"]


def test_caught_nested_transaction_failure_cannot_commit_partial_work():
    fixture = _seed_staff_mobile_v1()
    with pytest.raises(RuntimeError, match="取消本次保存"):
        with atomic_transaction():
            try:
                with transaction() as connection:
                    execute(connection, "UPDATE members SET referrer='rollback-test' WHERE id=?", (fixture["focal_member_id"],))
                    raise ValueError("failure")
            except ValueError:
                pass
    assert fetch_one("SELECT referrer FROM members WHERE id=?", (fixture["focal_member_id"],))["referrer"] != "rollback-test"
