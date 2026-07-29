from __future__ import annotations

import os
from dataclasses import dataclass


SAFE_ENVIRONMENTS = {"dev", "test", "staging"}


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str
    app_name: str
    database_url: str
    cors_origins: tuple[str, ...]
    allow_production_mutations: bool
    jwt_secret: str
    access_token_minutes: int
    refresh_token_days: int
    bootstrap_admin_username: str
    bootstrap_admin_password: str
    field_encryption_key: str
    integration_api_key: str
    deployment_read_only: bool
    run_bootstrap_on_startup: bool
    signin_api_base_url: str = ""
    signin_service_api_key: str = ""
    identity_authorization_enabled: bool = False
    identity_admin_writes_enabled: bool = False
    volunteer_service_invitations_enabled: bool = False
    class_roster_org_import_enabled: bool = False

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def assert_safe_startup(self) -> None:
        if (
            self.is_production
            and not self.allow_production_mutations
            and (not self.deployment_read_only or self.run_bootstrap_on_startup)
        ):
            raise RuntimeError(
                "生产环境写入未获批准：必须启用 DEPLOYMENT_READ_ONLY，"
                "或在获批任务中显式设置 ALLOW_PRODUCTION_MUTATIONS=true"
            )
        if self.app_env not in SAFE_ENVIRONMENTS | {"production"}:
            raise RuntimeError(f"未知 APP_ENV: {self.app_env}")
        if self.app_env in {"dev", "test"} and "production" in self.database_url.lower():
            raise RuntimeError("开发/测试环境禁止使用疑似生产数据库地址")
        if self.app_env in {"staging", "production"} and len(self.jwt_secret) < 32:
            raise RuntimeError("staging/production 的 JWT_SECRET 至少需要 32 个字符")
        if self.app_env in {"staging", "production"} and not self.field_encryption_key:
            raise RuntimeError("staging/production 必须配置 FIELD_ENCRYPTION_KEY")
        if (
            self.is_production
            and self.identity_admin_writes_enabled
            and not self.allow_production_mutations
        ):
            raise RuntimeError("生产身份管理写入未获批准")
        if (
            self.is_production
            and self.volunteer_service_invitations_enabled
            and not self.allow_production_mutations
        ):
            raise RuntimeError("生产志工服务邀请写入未获批准")


def get_settings() -> Settings:
    origins = tuple(
        item.strip()
        for item in os.getenv("CORS_ORIGINS", "http://localhost:8848").split(",")
        if item.strip()
    )
    return Settings(
        app_env=os.getenv("APP_ENV", "dev").strip().lower(),
        app_name=os.getenv("APP_NAME", "seiwajyuku-platform-api").strip(),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./data/seiwajyuku_dev.db").strip(),
        cors_origins=origins,
        allow_production_mutations=_bool("ALLOW_PRODUCTION_MUTATIONS"),
        jwt_secret=os.getenv("JWT_SECRET", "dev-only-secret-change-me-00000000"),
        access_token_minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "30")),
        refresh_token_days=int(os.getenv("REFRESH_TOKEN_DAYS", "7")),
        bootstrap_admin_username=os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin").strip(),
        bootstrap_admin_password=os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "").strip(),
        field_encryption_key=os.getenv("FIELD_ENCRYPTION_KEY", "").strip(),
        integration_api_key=os.getenv("INTEGRATION_API_KEY", "dev-integration-key").strip(),
        deployment_read_only=_bool("DEPLOYMENT_READ_ONLY"),
        run_bootstrap_on_startup=_bool("RUN_BOOTSTRAP_ON_STARTUP"),
        identity_authorization_enabled=_bool("IDENTITY_AUTHORIZATION_ENABLED"),
        identity_admin_writes_enabled=_bool("IDENTITY_ADMIN_WRITES_ENABLED"),
        volunteer_service_invitations_enabled=_bool(
            "VOLUNTEER_SERVICE_INVITATIONS_ENABLED"
        ),
        class_roster_org_import_enabled=_bool(
            "CLASS_ROSTER_ORG_IMPORT_ENABLED"
        ),
        signin_api_base_url=os.getenv("SIGNIN_API_BASE_URL", "").strip(),
        signin_service_api_key=os.getenv("SIGNIN_SERVICE_API_KEY", "").strip(),
    )
