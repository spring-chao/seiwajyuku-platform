from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.core.security import create_token, decode_token, token_hash
from app.core.settings import get_settings
from app.db import execute, fetch_one, transaction
from app.services.iam import authenticate, user_context


router = APIRouter(prefix="/api/v1", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


class LoginPayload(BaseModel):
    username: str
    password: str


class RefreshPayload(BaseModel):
    refresh_token: str


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    if not credentials:
        raise HTTPException(401, "需要登录")
    try:
        payload = decode_token(credentials.credentials, "access")
    except ValueError as exc:
        raise HTTPException(401, "登录已失效") from exc
    user = user_context(int(payload["sub"]))
    if not user or int(payload["ver"]) != int(user["token_version"]):
        raise HTTPException(401, "账户或令牌已失效")
    return user


def require_permission(permission: str):
    def dependency(user: dict = Depends(current_user)) -> dict:
        if permission not in user["permissions"]:
            raise HTTPException(403, "无此操作权限")
        return user

    return dependency


@router.post("/auth/login")
def login(payload: LoginPayload) -> dict:
    tokens = authenticate(payload.username, payload.password)
    if not tokens:
        raise HTTPException(401, "账号或密码错误")
    return {"success": True, "data": tokens}


@router.post("/auth/refresh")
def refresh(payload: RefreshPayload) -> dict:
    try:
        decoded = decode_token(payload.refresh_token, "refresh")
    except ValueError as exc:
        raise HTTPException(401, "刷新令牌无效") from exc
    stored = fetch_one(
        "SELECT id, user_id, expires_at, revoked_at FROM refresh_tokens WHERE token_hash=?",
        (token_hash(payload.refresh_token),),
    )
    user = user_context(int(decoded["sub"]))
    if not stored or stored["revoked_at"] or not user or int(decoded["ver"]) != user["token_version"]:
        raise HTTPException(401, "刷新令牌无效")
    settings = get_settings()
    access = create_token(
        user["id"], user["token_version"], "access", timedelta(minutes=settings.access_token_minutes)
    )
    return {"success": True, "data": {"access_token": access, "expires_in": settings.access_token_minutes * 60}}


@router.get("/me")
def me(user: dict = Depends(current_user)) -> dict:
    return {"success": True, "data": user}

