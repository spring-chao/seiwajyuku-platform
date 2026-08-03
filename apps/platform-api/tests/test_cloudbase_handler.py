from __future__ import annotations

import importlib
import os
import unittest
from dataclasses import replace


class CloudBaseHandlerTests(unittest.TestCase):
    def test_health_and_read_only_guard(self) -> None:
        original = os.environ.get("DEPLOYMENT_READ_ONLY")
        os.environ["DEPLOYMENT_READ_ONLY"] = "true"
        try:
            handler = importlib.import_module("handler")
            main_module = importlib.import_module("app.main")
            original_settings = main_module.settings
            main_module.settings = replace(
                original_settings, deployment_read_only=True
            )
            health = handler.main(
                {
                    "httpMethod": "GET",
                    "path": "/api/v1/health",
                    "headers": {},
                    "queryStringParameters": {},
                },
                object(),
            )
            self.assertEqual(health["statusCode"], 200)
            login = handler.main(
                {
                    "httpMethod": "POST",
                    "path": "/api/v1/auth/login",
                    "headers": {"content-type": "application/json"},
                    "body": "{}",
                },
                object(),
            )
            # Authentication remains reachable in read-only deployments. The
            # empty request is rejected by login validation, not the guard.
            self.assertEqual(login["statusCode"], 422)
            mutation = handler.main(
                {
                    "httpMethod": "POST",
                    "path": "/api/v1/members",
                    "headers": {"content-type": "application/json"},
                    "body": "{}",
                },
                object(),
            )
            self.assertEqual(mutation["statusCode"], 403)
        finally:
            if "main_module" in locals():
                main_module.settings = original_settings
            if original is None:
                os.environ.pop("DEPLOYMENT_READ_ONLY", None)
            else:
                os.environ["DEPLOYMENT_READ_ONLY"] = original


if __name__ == "__main__":
    unittest.main()
