import unittest

from fastapi.testclient import TestClient
from app.main import app


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
            },
        )


if __name__ == "__main__":
    unittest.main()
