from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


URL = "/api/v1/internal/study-evidence"


def test_cleanup_endpoint_requires_server_token(monkeypatch):
    monkeypatch.setenv("STUDY_EVIDENCE_CLEANUP_TOKEN", "t" * 64)
    with TestClient(app) as client:
        assert client.post(URL, json={}).status_code == 401
        assert client.post(
            URL,
            json={"limit": 1},
            headers={"X-Study-Evidence-Cleanup-Token": "wrong"},
        ).status_code == 401


def test_cleanup_endpoint_delegates_bounded_apply_to_service(monkeypatch):
    token = "t" * 64
    monkeypatch.setenv("STUDY_EVIDENCE_CLEANUP_TOKEN", token)
    report = {"candidates": 2, "deleted": 2, "errors": 0, "apply": True}
    with patch(
        "app.api.study_evidence_cleanup.cleanup_evidence", return_value=report
    ) as cleanup:
        with TestClient(app) as client:
            response = client.post(
                URL,
                json={"limit": 17},
                headers={"X-Study-Evidence-Cleanup-Token": token},
            )
    assert response.status_code == 200
    assert response.json() == {"success": True, "data": report}
    cleanup.assert_called_once_with(apply=True, limit=17)


def test_cleanup_endpoint_rejects_unknown_payload_and_hides_service_errors(monkeypatch):
    token = "t" * 64
    monkeypatch.setenv("STUDY_EVIDENCE_CLEANUP_TOKEN", token)
    with TestClient(app) as client:
        invalid = client.post(
            URL,
            json={"limit": 501},
            headers={"X-Study-Evidence-Cleanup-Token": token},
        )
    assert invalid.status_code == 422

    with patch(
        "app.api.study_evidence_cleanup.cleanup_evidence",
        side_effect=RuntimeError("secret-looking sdk details"),
    ):
        with TestClient(app) as client:
            failed = client.post(
                URL,
                json={},
                headers={"X-Study-Evidence-Cleanup-Token": token},
            )
    assert failed.status_code == 503
    assert failed.json() == {"detail": "合影清理暂不可用，请稍后重试"}
