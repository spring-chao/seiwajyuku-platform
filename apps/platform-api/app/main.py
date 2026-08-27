from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.agent_mcp import MCP_MOUNT_PATH, MCP_PATH, mcp_http_app, mcp_server
from app.api.auth import router as auth_router
from app.api.agent import authenticate_agent_headers, router as agent_router
from app.api.attendance import router as attendance_router
from app.api.checkin_rosters import router as checkin_rosters_router
from app.api.class_roster_preflight import router as class_roster_preflight_router
from app.api.class_roster_org_import import router as class_roster_org_import_router
from app.api.followups import router as followups_router
from app.api.iam import router as iam_router
from app.api.identity_admin import router as identity_admin_router
from app.api.integrations import router as integrations_router
from app.api.legacy_operations import router as legacy_operations_router
from app.api.member_care_actions import router as member_care_actions_router
from app.api.member_care_management import router as member_care_management_router
from app.api.imports import router as imports_router
from app.api.direct_class_preflight import router as direct_class_preflight_router
from app.api.direct_class_import import router as direct_class_import_router
from app.api.enrollment import router as enrollment_router
from app.api.portal import router as portal_router
from app.api.wechat import router as wechat_router
from app.api.study_meetings import router as study_meetings_router
from app.api.learning_plans import router as learning_plans_router
from app.api.member_roster_import import router as member_roster_import_router
from app.api.plans import router as plans_router
from app.api.operation_rhythm import router as operation_rhythm_router
from app.api.renewals import router as renewals_router
from app.api.members import router as members_router
from app.api.system import router as system_router
from app.core.settings import get_settings
from app.migrations import run_migrations
from app.services.iam import seed_iam


settings = get_settings()
settings.assert_safe_startup()
READ_ONLY_ALLOWED_POST_PATHS = frozenset({
    # Authentication must remain available while migration and admin writes are
    # closed. Login only updates authentication metadata/audit records; refresh
    # does not grant any permission beyond the existing account context.
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/class-roster-preflight/preview",
    "/api/v1/direct-class-preflight/preview",
    "/api/v1/member-roster-import/preview",
    # Renewal matching is also allowed in read-only production, but the
    # endpoint must remain ephemeral there: it parses and compares the two
    # workbooks without creating an import batch or staging rows.
    "/api/v1/renewals/imports/preview",
    # Legacy-system preview is memory-only and stores neither the uploaded
    # bundle nor its member activity facts. The apply endpoint remains blocked.
    "/api/v1/legacy-operations/preview",
    # The scheduled attendance pull is a key-authenticated integration write,
    # not a migration or administrative data-management write. Keeping this
    # path available preserves the existing weekday sync while all other POST
    # writes remain blocked in read-only mode.
    "/api/v1/attendance/sync/scheduled",
    # Agent/MCP read operations do not mutate business data. They are allowed
    # in a read-only deployment because every invocation is still audited.
    "/mcp/seiwajuku",
    "/api/agent/v1/members/match",
})


def read_only_request_allowed(method: str, path: str) -> bool:
    """Allow only explicitly audited, memory-only previews during production read-only mode."""
    if method in {"GET", "HEAD", "OPTIONS"}:
        return True
    return method == "POST" and (
        path in READ_ONLY_ALLOWED_POST_PATHS
        or path.startswith("/api/v1/members/")
        and path.endswith("/birthday-greeting-draft")
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        async with mcp_server.session_manager.run():
            # A read-only deployment must never run migrations or IAM seeding,
            # even if a stale service-level environment variable enables
            # bootstrap at runtime.
            if settings.run_bootstrap_on_startup and not settings.deployment_read_only:
                run_migrations()
                seed_iam()
            yield
    finally:
        # The SDK intentionally makes a session manager single-use in a
        # production process. Tests create several short-lived TestClients in
        # one process, so only the isolated test app may reset this private
        # lifecycle marker after the task group has shut down.
        if settings.app_env == "test":
            mcp_server.session_manager._has_started = False  # noqa: SLF001


app = FastAPI(
    title="盛和塾综合运营与发展建设平台 API",
    version="0.1.0",
    docs_url="/api/docs" if not settings.is_production else None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=[
        "Accept",
        "Authorization",
        "Content-Type",
        "Last-Event-ID",
        "MCP-Protocol-Version",
        "Mcp-Session-Id",
        "X-Request-ID",
        "X-API-Key",
        "X-Agent-Client-ID",
        "X-Agent-Client-Secret",
        "X-Agent-Channel",
        "X-Agent-Session-ID",
        "X-Requested-With",
    ],
    expose_headers=["Mcp-Session-Id"],
)


@app.middleware("http")
async def deployment_read_only_guard(request: Request, call_next):
    if settings.deployment_read_only and not read_only_request_allowed(
        request.method, request.url.path
    ):
        return JSONResponse(
            status_code=403,
            content={
                "detail": "当前为无持久数据库的只读部署验证实例，写入操作已禁用"
            },
        )
    return await call_next(request)


def _mcp_header_allowed(value: str | None, allowed: tuple[str, ...]) -> bool:
    if not value:
        return False
    if value in allowed:
        return True
    return any(
        item.endswith(":*") and value.startswith(item[:-2] + ":")
        for item in allowed
    )


@app.middleware("http")
async def mcp_transport_security_guard(request: Request, call_next):
    if request.url.path == MCP_PATH:
        if not _mcp_header_allowed(
            request.headers.get("host"), settings.mcp_allowed_hosts
        ):
            return JSONResponse(status_code=421, content={"detail": "Host 不在 MCP 允许范围"})
        origin = request.headers.get("origin")
        if origin and not _mcp_header_allowed(origin, settings.mcp_allowed_origins):
            return JSONResponse(status_code=403, content={"detail": "Origin 不在 MCP 允许范围"})
        try:
            request.state.agent_principal = authenticate_agent_headers(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


app.include_router(system_router)
app.include_router(auth_router)
app.include_router(agent_router)
app.include_router(iam_router)
app.include_router(identity_admin_router)
app.include_router(imports_router)
app.include_router(class_roster_preflight_router)
app.include_router(class_roster_org_import_router)
app.include_router(direct_class_preflight_router)
app.include_router(direct_class_import_router)
app.include_router(enrollment_router)
app.include_router(portal_router)
app.include_router(wechat_router)
app.include_router(study_meetings_router)
app.include_router(learning_plans_router)
app.include_router(member_roster_import_router)
app.include_router(plans_router)
app.include_router(operation_rhythm_router)
app.include_router(renewals_router)
app.include_router(members_router)
app.include_router(followups_router)
app.include_router(integrations_router)
app.include_router(legacy_operations_router)
app.include_router(member_care_actions_router)
app.include_router(member_care_management_router)
app.include_router(checkin_rosters_router)
app.include_router(attendance_router)
app.mount(MCP_MOUNT_PATH, mcp_http_app)
