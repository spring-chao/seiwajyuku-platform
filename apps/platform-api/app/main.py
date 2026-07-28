from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.attendance import router as attendance_router
from app.api.checkin_rosters import router as checkin_rosters_router
from app.api.followups import router as followups_router
from app.api.iam import router as iam_router
from app.api.integrations import router as integrations_router
from app.api.imports import router as imports_router
from app.api.plans import router as plans_router
from app.api.renewals import router as renewals_router
from app.api.members import router as members_router
from app.api.system import router as system_router
from app.core.settings import get_settings
from app.migrations import run_migrations
from app.services.iam import seed_iam


settings = get_settings()
settings.assert_safe_startup()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not (
        settings.deployment_read_only and not settings.run_bootstrap_on_startup
    ):
        run_migrations()
        seed_iam()
    yield


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
        "Authorization",
        "Content-Type",
        "X-Request-ID",
        "X-API-Key",
        "X-Requested-With",
    ],
)


@app.middleware("http")
async def deployment_read_only_guard(request: Request, call_next):
    if settings.deployment_read_only and request.method not in {"GET", "HEAD", "OPTIONS"}:
        return JSONResponse(
            status_code=403,
            content={
                "detail": "当前为无持久数据库的只读部署验证实例，写入操作已禁用"
            },
        )
    return await call_next(request)


app.include_router(system_router)
app.include_router(auth_router)
app.include_router(iam_router)
app.include_router(imports_router)
app.include_router(plans_router)
app.include_router(renewals_router)
app.include_router(members_router)
app.include_router(followups_router)
app.include_router(integrations_router)
app.include_router(checkin_rosters_router)
app.include_router(attendance_router)
