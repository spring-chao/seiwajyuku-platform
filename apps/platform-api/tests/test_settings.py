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
            run_bootstrap_on_startup=False,
            signin_api_base_url="",
            signin_service_api_key="",
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
            run_bootstrap_on_startup=False,
            signin_api_base_url="",
            signin_service_api_key="",
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
            run_bootstrap_on_startup=False,
            signin_api_base_url="",
            signin_service_api_key="",
        )
        settings.assert_safe_startup()

    def test_production_read_only_bootstrap_requires_write_approval(self) -> None:
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
            run_bootstrap_on_startup=True,
            signin_api_base_url="",
            signin_service_api_key="",
        )
        with self.assertRaises(RuntimeError):
            settings.assert_safe_startup()

    def test_production_identity_writes_require_mutation_approval(self) -> None:
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
            run_bootstrap_on_startup=False,
            signin_api_base_url="",
            signin_service_api_key="",
            identity_authorization_enabled=True,
            identity_admin_writes_enabled=True,
        )
        with self.assertRaisesRegex(RuntimeError, "身份管理写入未获批准"):
            settings.assert_safe_startup()

    def test_production_service_invitations_require_mutation_approval(self) -> None:
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
            run_bootstrap_on_startup=False,
            signin_api_base_url="",
            signin_service_api_key="",
            volunteer_service_invitations_enabled=True,
        )
        with self.assertRaisesRegex(RuntimeError, "志工服务邀请写入未获批准"):
            settings.assert_safe_startup()

    def test_production_signal_feedback_requires_mutation_approval(self) -> None:
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
            run_bootstrap_on_startup=False,
            signin_api_base_url="",
            signin_service_api_key="",
            member_service_signal_feedback_enabled=True,
        )
        with self.assertRaisesRegex(RuntimeError, "服务提示反馈写入未获批准"):
            settings.assert_safe_startup()

    def test_local_wechat_provider_mode_is_rejected_outside_dev_and_test(self) -> None:
        settings = Settings(
            app_env="staging",
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
            run_bootstrap_on_startup=False,
            signin_api_base_url="",
            signin_service_api_key="",
            wechat_local_test_mode=True,
        )
        with self.assertRaisesRegex(RuntimeError, "WECHAT_LOCAL_TEST_MODE"):
            settings.assert_safe_startup()

    def test_enabled_cleanup_requires_a_server_side_token(self) -> None:
        settings = Settings(
            app_env="staging",
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
            deployment_read_only=False,
            run_bootstrap_on_startup=False,
            signin_api_base_url="",
            signin_service_api_key="",
            study_evidence_cleanup_enabled=True,
            study_evidence_cleanup_token="too-short",
        )
        with self.assertRaisesRegex(RuntimeError, "CLEANUP_TOKEN.*32"):
            settings.assert_safe_startup()


if __name__ == "__main__":
    unittest.main()
