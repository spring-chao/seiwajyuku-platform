import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.db import execute, fetch_all, fetch_one, transaction
from app.main import app
from app.services.course_credit_rules import (
    create_course_credit_rule_version,
    list_course_credit_rules,
    update_course_credit_rule,
)
from app.services.audit import write_audit


def _actor_id() -> int:
    row = fetch_one("SELECT id FROM app_users ORDER BY id LIMIT 1")
    assert row
    return int(row["id"])


def test_catalog_has_first_year_defaults_and_pending_new_courses():
    data = list_course_credit_rules()
    assert data["version_status"] == "DRAFT"
    assert data["persisted"] is False
    rules = {item["course_key"]: item for item in data["rules"]}
    assert rules["Y1-KYOCERA-ANNUAL-PLAN"]["credit_points"] == 30
    assert rules["Y1-KYOCERA-ANNUAL-PLAN"]["status"] == "CONFIGURED"
    assert rules["AUTO-QR-AMOEBA-INTRODUCTION"]["credit_points"] == 0
    assert rules["AUTO-QR-AMOEBA-INTRODUCTION"]["status"] == "PENDING"
    assert data["custom_credit_allowed"] is True


def test_update_is_versioned_and_audited():
    actor_id = _actor_id()
    result = update_course_credit_rule(
        actor_user_id=actor_id,
        plan_key="STANDARD_3Y_2026",
        version_label="2026.1",
        course_key="AUTO-QR-AMOEBA-INTRODUCTION",
        course_name="阿米巴经营之概论",
        credit_points=20,
        status="CONFIGURED",
    )
    updated = result["updated_rule"]
    assert updated["credit_points"] == 20
    assert updated["status"] == "CONFIGURED"
    assert result["persisted"] is True
    audit = fetch_one(
        "SELECT action, resource_type, before_json, after_json FROM audit_logs "
        "WHERE action='learning_plan.credit_rule.update' ORDER BY id DESC LIMIT 1"
    )
    assert audit
    assert audit["resource_type"] == "learning_plan_credit_rule"
    assert '"credit_points": 20' in audit["after_json"]


def test_audit_serializes_mysql_datetime_snapshots():
    actor_id = _actor_id()
    updated_at = datetime(2026, 8, 26, 12, 5, 31, tzinfo=UTC)
    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=actor_id,
            action="learning_plan.credit_rule.datetime_smoke",
            resource_type="learning_plan_credit_rule",
            before={"updated_at": updated_at},
            after={"updated_at": updated_at},
        )
    audit = fetch_one(
        "SELECT before_json, after_json FROM audit_logs "
        "WHERE action='learning_plan.credit_rule.datetime_smoke' "
        "ORDER BY id DESC LIMIT 1"
    )
    assert audit
    assert json.loads(audit["before_json"])["updated_at"] == updated_at.isoformat()
    assert json.loads(audit["after_json"])["updated_at"] == updated_at.isoformat()


def test_published_version_cannot_be_overwritten_and_new_version_clones():
    actor_id = _actor_id()
    with transaction() as connection:
        execute(
            connection,
            "UPDATE learning_plan_credit_rule_versions SET status='PUBLISHED' WHERE plan_key=? AND version_label=?",
            ("STANDARD_3Y_2026", "2026.1"),
        )
    with pytest.raises(ValueError, match="不可修改"):
        update_course_credit_rule(
            actor_user_id=actor_id,
            plan_key="STANDARD_3Y_2026",
            version_label="2026.1",
            course_key="AUTO-QR-AMOEBA-INTRODUCTION",
            credit_points=30,
        )
    cloned = create_course_credit_rule_version(
        actor_user_id=actor_id,
        plan_key="STANDARD_3Y_2026",
        version_label="2026.2",
        based_on_version_label="2026.1",
    )
    assert cloned["version_status"] == "DRAFT"
    assert len(cloned["rules"]) >= 14
    assert cloned["rules"][-1]["persisted"] is True


def test_credit_config_api_is_protected_and_supports_new_version():
    with TestClient(app) as client:
        assert client.get("/api/v1/learning-plan-course-credit-config").status_code == 401
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "test-admin-password"},
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        response = client.get("/api/v1/learning-plan-course-credit-config", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["available_credit_points"] == [0, 15, 20, 30, 40]
        version = client.post(
            "/api/v1/learning-plan-course-credit-config/versions",
            headers=headers,
            json={"version_label": "api-test", "based_on_version_label": "2026.1"},
        )
        assert version.status_code == 200, version.text
        update = client.put(
            "/api/v1/learning-plan-course-credit-config/AUTO-QR-AMOEBA-INTRODUCTION",
            headers=headers,
            json={"version_label": "api-test", "credit_points": 30, "status": "CONFIGURED"},
        )
        assert update.status_code == 200, update.text
        assert update.json()["data"]["updated_rule"]["credit_points"] == 30
