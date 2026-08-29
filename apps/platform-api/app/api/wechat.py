from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.services.wechat_identity import (
    WeChatIdentityError,
    WeChatProviderError,
    resolve_member_session,
    revoke_member_binding,
    verify_member_binding,
)
from app.services.volunteer_positions import get_member_volunteer_services


router = APIRouter(prefix="/api/v1/wechat", tags=["wechat-identity"])
bearer = HTTPBearer(auto_error=False)


class MemberBindingVerifyPayload(BaseModel):
    code: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=6, max_length=32)


def _ensure_enabled() -> None:
    if not get_settings().wechat_member_binding_enabled:
        raise HTTPException(404, "微信学员身份功能尚未开启")


def _session_token(
    credentials: HTTPAuthorizationCredentials | None,
) -> str:
    _ensure_enabled()
    if not credentials:
        raise HTTPException(401, "需要绑定微信学员身份")
    return credentials.credentials


@router.post("/member-bindings/verify")
def verify_binding(payload: MemberBindingVerifyPayload) -> dict:
    _ensure_enabled()
    try:
        data = verify_member_binding(
            code=payload.code,
            name=payload.name,
            phone=payload.phone,
        )
    except WeChatProviderError as exc:
        raise HTTPException(503, str(exc)) from exc
    except WeChatIdentityError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/me")
def wechat_me(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    token = _session_token(credentials)
    try:
        data = resolve_member_session(token)
    except WeChatIdentityError as exc:
        raise HTTPException(401, str(exc)) from exc
    # The provider credential and binding id remain server-side details.
    return {"success": True, "data": {"member": data["member"]}}


@router.get("/volunteer-services")
def volunteer_services(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    """Return current volunteer identity independently of study-meeting context."""

    token = _session_token(credentials)
    try:
        session = resolve_member_session(token)
        data = get_member_volunteer_services(session["member_id"])
    except WeChatIdentityError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/member-bindings/revoke")
def revoke_binding(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    token = _session_token(credentials)
    try:
        data = revoke_member_binding(token)
    except WeChatIdentityError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {"success": True, "data": data}
