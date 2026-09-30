"""Audit driver-type compatibility without changing transaction semantics."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.db import execute, transaction
from app.services.audit import _audit_json, write_audit


def test_nested_decimal_and_dates_are_exact_json_values():
    original = {"points": Decimal("9007199254740993.01"), "negative": Decimal("-4.00"),
                "nested": [Decimal("0.00000100"), date(2026, 10, 1)],
                "timestamp": datetime(2026, 10, 1, tzinfo=UTC)}
    encoded = json.loads(_audit_json(original))
    assert encoded == {"points": "9007199254740993.01", "negative": "-4.00",
                       "nested": ["0.00000100", "2026-10-01"], "timestamp": "2026-10-01T00:00:00+00:00"}
    assert original["points"] == Decimal("9007199254740993.01")


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_non_finite_decimal_does_not_silently_enter_audit(value):
    with pytest.raises(TypeError, match="Non-finite Decimal"):
        _audit_json({"points": Decimal(value)})


def test_unknown_objects_still_fail_closed_and_none_stays_absent():
    assert _audit_json(None) is None
    with pytest.raises(TypeError, match="not JSON serializable"):
        _audit_json({"unknown": object()})


def test_write_audit_persists_exact_decimal_snapshots():
    with transaction() as connection:
        write_audit(connection, actor_user_id=None, action="test.decimal.snapshot",
                    resource_type="test", resource_id="synthetic-decimal",
                    before={"points": Decimal("4.00")}, after={"points": Decimal("-4.00")})
        row = execute(connection, "SELECT before_json,after_json FROM audit_logs "
                      "WHERE action='test.decimal.snapshot' AND resource_id='synthetic-decimal' ORDER BY id DESC LIMIT 1").fetchone()
    assert json.loads(row["before_json"]) == {"points": "4.00"}
    assert json.loads(row["after_json"]) == {"points": "-4.00"}
