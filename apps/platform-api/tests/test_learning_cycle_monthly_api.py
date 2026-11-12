from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.learning_cycle_monthly import router

URL = "/api/v1/internal/learning-cycle-monthly"
TOKEN = "synthetic-maintenance-token-" * 3
app = FastAPI()
app.include_router(router)


def test_monthly_scheduler_rejects_missing_or_wrong_token_before_service(monkeypatch):
    monkeypatch.setenv("STUDY_EVIDENCE_CLEANUP_TOKEN", TOKEN)
    with patch("app.api.learning_cycle_monthly.refresh_due_class_meetings") as sweep, TestClient(app) as client:
        assert client.post(URL, json={}).status_code == 401
        assert client.post(URL, json={}, headers={"X-Study-Evidence-Cleanup-Token": "wrong"}).status_code == 401
        assert client.get(URL).status_code == 405
    sweep.assert_not_called()


@pytest.mark.parametrize("payload", [{"at": "2027-01-01"}, {"class_id": "other"}, {"startup_repair": True}])
def test_monthly_scheduler_cannot_override_clock_scope_or_repair(monkeypatch, payload):
    monkeypatch.setenv("STUDY_EVIDENCE_CLEANUP_TOKEN", TOKEN)
    with patch("app.api.learning_cycle_monthly.refresh_due_class_meetings") as sweep, TestClient(app) as client:
        response = client.post(URL, json=payload, headers={"X-Study-Evidence-Cleanup-Token": TOKEN})
    assert response.status_code == 422
    sweep.assert_not_called()


@pytest.mark.parametrize("failed", [0, 1])
def test_monthly_scheduler_finishes_sweep_before_responding_and_hides_errors(monkeypatch, failed):
    monkeypatch.setenv("STUDY_EVIDENCE_CLEANUP_TOKEN", TOKEN)
    report = {"enabled": True, "scanned": 19, "updated": 0, "failed": failed, "repair_required": 0, "review_required": 0}
    with patch("app.api.learning_cycle_monthly.refresh_due_class_meetings", return_value=report) as sweep, TestClient(app) as client:
        response = client.post(URL, json={}, headers={"X-Study-Evidence-Cleanup-Token": TOKEN})
    assert response.json()['success'] == (failed == 0)
    assert response.json()['data'] == report
    assert response.json()['renewal_status_sync']['failed'] == 0
    sweep.assert_called_once_with()
    with patch("app.api.learning_cycle_monthly.refresh_due_class_meetings", side_effect=RuntimeError("secret upstream details")), TestClient(app) as client:
        response = client.post(URL, json={}, headers={"X-Study-Evidence-Cleanup-Token": TOKEN})
    assert response.status_code == 503
    assert response.json() == {"detail": "班级月更暂不可用，请稍后重试"}


@pytest.mark.parametrize("gates", [

    {"LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED": "true", "DEPLOYMENT_READ_ONLY": "true"},
    {"APP_ENV": "production", "LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED": "true", "DEPLOYMENT_READ_ONLY": "false", "ALLOW_PRODUCTION_MUTATIONS": "false"},
])
def test_monthly_scheduler_keeps_existing_write_gates(monkeypatch, gates):
    monkeypatch.setenv("STUDY_EVIDENCE_CLEANUP_TOKEN", TOKEN)
    for key, value in gates.items():
        monkeypatch.setenv(key, value)
    with patch("app.services.class_meeting_next_day._load_groups") as refresh, TestClient(app) as client:
        response = client.post(URL, json={}, headers={"X-Study-Evidence-Cleanup-Token": TOKEN})
    assert response.json()["data"]["updated"] == 0
    refresh.assert_not_called()
