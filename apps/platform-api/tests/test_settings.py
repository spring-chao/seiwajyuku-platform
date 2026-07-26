import unittest

from app.core.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_production_requires_explicit_mutation_approval(self) -> None:
        settings = Settings(
            app_env="production",
            app_name="test",
            database_url="mysql+pymysql://example",
            cors_origins=(),
            allow_production_mutations=False,
            jwt_secret="x" * 32,
            access_token_minutes=30,
            refresh_token_days=7,
            bootstrap_admin_username="admin",
            bootstrap_admin_password="",
            field_encryption_key="",
            integration_api_key="test-integration-key",
            deployment_read_only=False,
        )
        with self.assertRaisesRegex(RuntimeError, "生产环境写入未获批准"):
            settings.assert_safe_startup()

    def test_dev_rejects_production_named_database(self) -> None:
        settings = Settings(
            app_env="dev",
            app_name="test",
            database_url="mysql+pymysql://localhost/seiwajyuku_production",
            cors_origins=(),
            allow_production_mutations=False,
            jwt_secret="x" * 32,
            access_token_minutes=30,
            refresh_token_days=7,
            bootstrap_admin_username="admin",
            bootstrap_admin_password="",
            field_encryption_key="",
            integration_api_key="test-integration-key",
            deployment_read_only=False,
        )
        with self.assertRaisesRegex(RuntimeError, "禁止使用疑似生产数据库"):
            settings.assert_safe_startup()

    def test_production_read_only_probe_can_start_without_write_approval(self) -> None:
        settings = Settings(
            app_env="production",
            app_name="test",
            database_url="mysql+pymysql://example",
            cors_origins=(),
            allow_production_mutations=False,
            jwt_secret="x" * 32,
            access_token_minutes=30,
            refresh_token_days=7,
            bootstrap_admin_username="admin",
            bootstrap_admin_password="",
            field_encryption_key="configured-outside-source-control",
            integration_api_key="test-integration-key",
            deployment_read_only=True,
        )
        settings.assert_safe_startup()


if __name__ == "__main__":
    unittest.main()
