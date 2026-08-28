import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.main import app, read_only_request_allowed


class SystemApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_liveness_does_not_require_database_query(self) -> None:
        response = self.client.get("/health/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_environment_is_not_production(self) -> None:
        response = self.client.get("/api/v1/system/environment")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "environment": "test",
                "production": False,
                "production_mutations_allowed": False,
                "deployment_read_only": False,
                "identity_authorization_enabled": True,
                "identity_admin_writes_enabled": True,
                "volunteer_service_invitations_enabled": True,
                "member_service_signal_feedback_enabled": True,
            },
        )

    def test_build_info_exposes_only_allowlisted_provenance(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_VERSION": "ci-test",
                "APP_GIT_SHA": "a" * 40,
                "APP_BUILD_TIME_UTC": "2026-08-28T05:00:00Z",
                "APP_BUILD_ID": "ci-123",
                "APP_IMAGE_DIGEST": "sha256:" + "b" * 64,
                "DATABASE_URL": "mysql://should-never-be-returned",
                "JWT_SECRET": "secret-should-never-be-returned",
            },
        ):
            response = self.client.get("/api/v1/system/build-info")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "version": "ci-test",
                "commit_sha": "a" * 40,
                "build_time_utc": "2026-08-28T05:00:00Z",
                "build_id": "ci-123",
                "image_digest": "sha256:" + "b" * 64,
                "environment": "test",
            },
        )
        self.assertNotIn("DATABASE_URL", response.json())
        self.assertNotIn("JWT_SECRET", response.json())

    def test_build_info_compatibility_aliases_match(self) -> None:
        with patch.dict(
            os.environ,
            {"APP_GIT_SHA": "c" * 40, "APP_VERSION": "alias-test"},
        ):
            canonical = self.client.get("/api/v1/system/build-info")
            json_alias = self.client.get("/build-info.json")
            version_alias = self.client.get("/api/version")

        self.assertEqual(canonical.status_code, 200)
        self.assertEqual(json_alias.status_code, 200)
        self.assertEqual(version_alias.status_code, 200)
        self.assertEqual(canonical.json(), json_alias.json())
        self.assertEqual(canonical.json(), version_alias.json())

    def test_legacy_merge_preview_is_read_only_but_apply_is_not(self) -> None:
        self.assertTrue(
            read_only_request_allowed("POST", "/api/v1/legacy-operations/preview")
        )
        self.assertTrue(
            read_only_request_allowed(
                "POST", "/api/v1/members/1/birthday-greeting-draft"
            )
        )
        self.assertTrue(
            read_only_request_allowed(
                "GET", "/api/v1/operations/rhythm/snapshot"
            )
        )
        self.assertFalse(
            read_only_request_allowed(
                "POST", "/api/v1/operations/rhythm/generate"
            )
        )
        self.assertFalse(
            read_only_request_allowed(
                "PATCH", "/api/v1/operations/rhythm/items/1"
            )
        )
        self.assertFalse(
            read_only_request_allowed("POST", "/api/v1/legacy-operations/apply")
        )
        self.assertFalse(
            read_only_request_allowed(
                "POST", "/api/v1/members/1/service-signals/CONTACT_INFO_REVIEW/feedback"
            )
        )


if __name__ == "__main__":
    unittest.main()
