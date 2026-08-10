from fastapi import APIRouter

from app.db import fetch_one


router = APIRouter(tags=["system"])


@router.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "ok", "service": "seiwajyuku-platform-api"}


@router.get("/health")
@router.get("/api/v1/health")
def health() -> dict[str, str]:
    fetch_one("SELECT 1 AS ok")
    return {"status": "ok", "service": "seiwajyuku-platform-api"}


@router.get("/api/v1/system/environment")
def environment() -> dict[str, str | bool]:
    from app.core.settings import get_settings

    settings = get_settings()
    return {
        "environment": settings.app_env,
        "production": settings.is_production,
        "production_mutations_allowed": settings.allow_production_mutations,
        "deployment_read_only": settings.deployment_read_only,
        "identity_authorization_enabled": settings.identity_authorization_enabled,
        "identity_admin_writes_enabled": settings.identity_admin_writes_enabled,
        "volunteer_service_invitations_enabled": settings.volunteer_service_invitations_enabled,
        "member_service_signal_feedback_enabled": (
            settings.member_service_signal_feedback_enabled
        ),
    }
