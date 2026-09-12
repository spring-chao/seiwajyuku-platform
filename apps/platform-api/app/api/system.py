from fastapi import APIRouter

from app.core.build_info import get_build_info
from app.db import fetch_one


router = APIRouter(tags=["system"])


@router.get("/__tcb_probe__")
def cloudbase_probe() -> dict[str, str]:
    """CloudBase readiness probe; deliberately independent of the database."""
    return {"status": "ok"}


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
        # These are non-sensitive, read-only rollout indicators.  Returning
        # the values observed by this process makes it possible to distinguish
        # a CloudBase control-plane update from an instance that has actually
        # reloaded its runtime environment.
        "wechat_member_binding_enabled": settings.wechat_member_binding_enabled,
        "wechat_staff_mobile_operations_enabled": (
            settings.wechat_staff_mobile_operations_enabled
        ),
        "wechat_local_test_mode": settings.wechat_local_test_mode,
    }


@router.get("/api/v1/system/build-info")
@router.get("/build-info.json")
@router.get("/api/version")
def build_info() -> dict[str, str]:
    """Expose only the immutable, non-sensitive API build provenance."""

    return get_build_info()
