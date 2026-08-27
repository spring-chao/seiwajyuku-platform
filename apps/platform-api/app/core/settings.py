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
    public_enrollment_rate_limit: int = 12
    public_enrollment_rate_window_seconds: int = 300
    wechat_miniprogram_app_id: str = ""
    wechat_miniprogram_app_secret: str = ""
    wechat_miniprogram_page: str = "pages/enrollment/index"
    signin_api_base_url: str = ""
    signin_service_api_key: str = ""
    identity_authorization_enabled: bool = False
    identity_admin_writes_enabled: bool = False
    volunteer_service_invitations_enabled: bool = False
    class_roster_org_import_enabled: bool = False
    member_service_signal_feedback_enabled: bool = False
    agent_api_enabled: bool = False
    # V1.2 unified mini-program capabilities remain explicitly closed by
    # default.  Each capability is opened independently after local/staging
    # validation and a bounded release approval.
    # This provider stub is only for isolated dev/test UX acceptance. Startup
    # safety rejects it in every deployable environment.
    wechat_local_test_mode: bool = False
    wechat_member_binding_enabled: bool = False
    study_meeting_submission_enabled: bool = False
    study_meeting_review_enabled: bool = False
    study_meeting_evidence_enabled: bool = False
    study_meeting_course_edit_enabled: bool = False
    study_evidence_retention_hours: int = 168
    learning_credit_settlement_enabled: bool = False
    agent_client_id: str = ""
    agent_client_secret: str = ""
    agent_allowed_channels: tuple[str, ...] = ("api", "wecom", "wechat")
    mcp_allowed_hosts: tuple[str, ...] = (
        "testserver",
        "localhost:*",
        "127.0.0.1:*",
    )
    mcp_allowed_origins: tuple[str, ...] = ()
    agent_allowed_tools: tuple[str, ...] = (
        "get_my_today_actions",
        "find_member",
        "get_member_summary",
        "get_member_timeline",
        "get_renewal_context",
        "get_followup_context",
    )

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
        if self.wechat_local_test_mode and self.app_env not in {"dev", "test"}:
            raise RuntimeError("WECHAT_LOCAL_TEST_MODE 仅允许 dev/test 环境")
        if self.study_evidence_retention_hours < 1:
            raise RuntimeError("STUDY_EVIDENCE_RETENTION_HOURS 必须大于0")
        if self.study_meeting_evidence_enabled and self.app_env not in {"dev", "test"}:
            if os.getenv("STUDY_EVIDENCE_STORAGE_BACKEND", "local") != "cos":
                raise RuntimeError("部署环境学习合影必须使用独立私有 COS 存储")
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
        if (
            self.is_production
            and self.member_service_signal_feedback_enabled
            and not self.allow_production_mutations
        ):
            raise RuntimeError("生产学员服务提示反馈写入未获批准")
        if self.agent_api_enabled and (
            not self.agent_client_id or len(self.agent_client_secret) < 32
        ):
            raise RuntimeError(
                "Agent API 已启用：必须配置非空 AGENT_CLIENT_ID 和至少32位 AGENT_CLIENT_SECRET"
            )
        if self.public_enrollment_rate_limit < 1:
            raise RuntimeError("公开入塾申请限流次数必须大于0")
        if self.public_enrollment_rate_window_seconds < 1:
            raise RuntimeError("公开入塾申请限流窗口必须大于0秒")


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
        public_enrollment_rate_limit=int(
            os.getenv("PUBLIC_ENROLLMENT_RATE_LIMIT", "12")
        ),
        public_enrollment_rate_window_seconds=int(
            os.getenv("PUBLIC_ENROLLMENT_RATE_WINDOW_SECONDS", "300")
        ),
        wechat_miniprogram_app_id=os.getenv("WECHAT_MINIPROGRAM_APP_ID", "").strip(),
        wechat_miniprogram_app_secret=os.getenv(
            "WECHAT_MINIPROGRAM_APP_SECRET", ""
        ).strip(),
        wechat_miniprogram_page=os.getenv(
            "WECHAT_MINIPROGRAM_PAGE", "pages/enrollment/index"
        ).strip(),
        identity_authorization_enabled=_bool("IDENTITY_AUTHORIZATION_ENABLED"),
        identity_admin_writes_enabled=_bool("IDENTITY_ADMIN_WRITES_ENABLED"),
        volunteer_service_invitations_enabled=_bool(
            "VOLUNTEER_SERVICE_INVITATIONS_ENABLED"
        ),
        class_roster_org_import_enabled=_bool(
            "CLASS_ROSTER_ORG_IMPORT_ENABLED"
        ),
        member_service_signal_feedback_enabled=_bool(
            "MEMBER_SERVICE_SIGNAL_FEEDBACK_ENABLED"
        ),
        agent_api_enabled=_bool("AGENT_API_ENABLED"),
        wechat_local_test_mode=_bool("WECHAT_LOCAL_TEST_MODE"),
        wechat_member_binding_enabled=_bool("WECHAT_MEMBER_BINDING_ENABLED"),
        study_meeting_submission_enabled=_bool("STUDY_MEETING_SUBMISSION_ENABLED"),
        study_meeting_review_enabled=_bool("STUDY_MEETING_REVIEW_ENABLED"),
        study_meeting_evidence_enabled=_bool("STUDY_MEETING_EVIDENCE_ENABLED"),
        study_meeting_course_edit_enabled=_bool("STUDY_MEETING_COURSE_EDIT_ENABLED"),
        study_evidence_retention_hours=int(os.getenv("STUDY_EVIDENCE_RETENTION_HOURS", "168")),
        learning_credit_settlement_enabled=_bool(
            "LEARNING_CREDIT_SETTLEMENT_ENABLED"
        ),
        agent_client_id=os.getenv("AGENT_CLIENT_ID", "").strip(),
        agent_client_secret=os.getenv("AGENT_CLIENT_SECRET", "").strip(),
        agent_allowed_channels=tuple(
            item.strip().lower()
            for item in os.getenv(
                "AGENT_ALLOWED_CHANNELS", "api,wecom,wechat"
            ).split(",")
            if item.strip()
        ),
        mcp_allowed_hosts=tuple(
            item.strip()
            for item in os.getenv(
                "MCP_ALLOWED_HOSTS", "testserver,localhost:*,127.0.0.1:*"
            ).split(",")
            if item.strip()
        ),
        mcp_allowed_origins=tuple(
            item.strip()
            for item in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",")
            if item.strip()
        ),
        agent_allowed_tools=tuple(
            item.strip()
            for item in os.getenv(
                "AGENT_ALLOWED_TOOLS",
                "get_my_today_actions,find_member,get_member_summary,get_member_timeline,"
                "get_renewal_context,get_followup_context",
            ).split(",")
            if item.strip()
        ),
        signin_api_base_url=os.getenv("SIGNIN_API_BASE_URL", "").strip(),
        signin_service_api_key=os.getenv("SIGNIN_SERVICE_API_KEY", "").strip(),
    )
