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
    with patch("app.api.learning_cycle_monthly.monthly.refresh_sweep") as sweep, TestClient(app) as client:
        assert client.post(URL, json={}).status_code == 401
        assert client.post(URL, json={}, headers={"X-Study-Evidence-Cleanup-Token": "wrong"}).status_code == 401
        assert client.get(URL).status_code == 405
    sweep.assert_not_called()


@pytest.mark.parametrize("payload", [{"at": "2027-01-01"}, {"class_id": "other"}, {"startup_repair": True}])
def test_monthly_scheduler_cannot_override_clock_scope_or_repair(monkeypatch, payload):
    monkeypatch.setenv("STUDY_EVIDENCE_CLEANUP_TOKEN", TOKEN)
    with patch("app.api.learning_cycle_monthly.monthly.refresh_sweep") as sweep, TestClient(app) as client:
        response = client.post(URL, json=payload, headers={"X-Study-Evidence-Cleanup-Token": TOKEN})
    assert response.status_code == 422
    sweep.assert_not_called()


@pytest.mark.parametrize("failed", [0, 1])
def test_monthly_scheduler_finishes_sweep_before_responding_and_hides_errors(monkeypatch, failed):
    monkeypatch.setenv("STUDY_EVIDENCE_CLEANUP_TOKEN", TOKEN)
    report = {"enabled": True, "scanned": 19, "updated": 0, "failed": failed, "repair_required": 0, "review_required": 0}
    with patch("app.api.learning_cycle_monthly.monthly.refresh_sweep", return_value=report) as sweep, TestClient(app) as client:
        response = client.post(URL, json={}, headers={"X-Study-Evidence-Cleanup-Token": TOKEN})
    assert response.json() == {"success": failed == 0, "data": report}
    sweep.assert_called_once_with()
    with patch("app.api.learning_cycle_monthly.monthly.refresh_sweep", side_effect=RuntimeError("secret upstream details")), TestClient(app) as client:
        response = client.post(URL, json={}, headers={"X-Study-Evidence-Cleanup-Token": TOKEN})
    assert response.status_code == 503
    assert response.json() == {"detail": "班级月更暂不可用，请稍后重试"}


@pytest.mark.parametrize("gates", [
    {"LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED": "false"},
    {"LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED": "true", "DEPLOYMENT_READ_ONLY": "true"},
    {"APP_ENV": "production", "LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED": "true", "DEPLOYMENT_READ_ONLY": "false", "ALLOW_PRODUCTION_MUTATIONS": "false"},
])
def test_monthly_scheduler_keeps_existing_write_gates(monkeypatch, gates):
    monkeypatch.setenv("STUDY_EVIDENCE_CLEANUP_TOKEN", TOKEN)
    for key, value in gates.items():
        monkeypatch.setenv(key, value)
    with patch("app.services.learning_cycle_monthly.refresh_all") as refresh, TestClient(app) as client:
        response = client.post(URL, json={}, headers={"X-Study-Evidence-Cleanup-Token": TOKEN})
    assert response.json()["data"]["enabled"] is False
    refresh.assert_not_called()
