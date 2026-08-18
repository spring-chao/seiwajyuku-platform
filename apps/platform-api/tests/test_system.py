import unittest

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
