from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.auth import current_user
from app.api import production_actions
from app.main import app
from app.services.course_credit_reconciliation import UNUSED_PLACEHOLDER_KEYS
from app.services.production_operations import REQUIRED_PERMISSION


def _payload() -> dict:
    return {
        "expected_release_commit": "c" * 40,
        "expected_production_fingerprint": "a" * 64,
        "expected_canonical_fingerprint": "b" * 64,
        "expected_course_rule_version_id": 3,
        "expected_course_rule_status": "DRAFT",
        "expected_rule_count": 14,
        "expected_placeholder_keys": sorted(UNUSED_PLACEHOLDER_KEYS),
        "execution_reason": "approved G5.4-C3.2 verification",
    }


def _call_as(monkeypatch, user: dict, *, enabled: bool = True):
    monkeypatch.setenv("G5_4_PRODUCTION_RULE_APPLY_ENABLED", "true" if enabled else "false")
    app.dependency_overrides[current_user] = lambda: user
    monkeypatch.setattr(
        production_actions,
        "apply_g5_4_course_rule_reconciliation",
        lambda **kwargs: {
            "status": "APPLIED",
            "actor_user_id": kwargs["actor_user_id"],
        },
    )
    try:
        with TestClient(app) as client:
            return client.post(
                "/api/v1/ops/production-actions/g5-4-course-rule-reconciliation/apply",
                json=_payload(),
            )
    finally:
        app.dependency_overrides.clear()


def test_feature_flag_false_hides_endpoint_from_system_admin(monkeypatch) -> None:
    response = _call_as(
        monkeypatch,
        {"id": 1, "roles": ["system_admin"], "permissions": [REQUIRED_PERMISSION]},
        enabled=False,
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "FEATURE_DISABLED"


def test_ordinary_user_and_operations_roles_cannot_apply(monkeypatch) -> None:
    ordinary = _call_as(
        monkeypatch,
        {"id": 2, "roles": ["read_only"], "permissions": []},
    )
    assert ordinary.status_code == 403

    operations = _call_as(
        monkeypatch,
        {"id": 3, "roles": ["operations_admin"], "permissions": [REQUIRED_PERMISSION]},
    )
    assert operations.status_code == 403
    assert operations.json()["detail"]["code"] == "PERMISSION_DENIED"

    center_learning = _call_as(
        monkeypatch,
        {"id": 4, "roles": ["ops_center_learning"], "permissions": [REQUIRED_PERMISSION]},
    )
    assert center_learning.status_code == 403


def test_system_admin_with_enabled_flag_reaches_only_registered_operation(monkeypatch) -> None:
    response = _call_as(
        monkeypatch,
        {"id": 9, "roles": ["system_admin"], "permissions": [REQUIRED_PERMISSION]},
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"status": "APPLIED", "actor_user_id": 9}


def test_payload_forbids_sql_and_unknown_fields(monkeypatch) -> None:
    monkeypatch.setenv("G5_4_PRODUCTION_RULE_APPLY_ENABLED", "true")
    app.dependency_overrides[current_user] = lambda: {
        "id": 9,
        "roles": ["system_admin"],
        "permissions": [REQUIRED_PERMISSION],
    }
    payload = _payload()
    payload["sql"] = "DELETE FROM learning_plan_credit_rules"
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/ops/production-actions/g5-4-course-rule-reconciliation/apply",
                json=payload,
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_production_action_is_not_exposed_in_openapi(monkeypatch) -> None:
    monkeypatch.setenv("G5_4_PRODUCTION_RULE_APPLY_ENABLED", "true")
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
    assert "/api/v1/ops/production-actions/g5-4-course-rule-reconciliation/apply" not in schema["paths"]
