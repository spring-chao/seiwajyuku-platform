"""Activity-specific entry codes contain an opaque route token, never identity."""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import httpx

from app.db import execute, fetch_one, transaction
from app.services.audit import write_audit
from app.services.signin_engine import SigninEngineError, enabled, event_detail
from app.services.wechat_identity import _wechat_access_token, WeChatProviderError


PAGE = "pages/checkin/index"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def resolve_entry(token: str) -> str:
    row = fetch_one("SELECT event_id, expires_at, revoked_at FROM attendance_entry_tokens WHERE token_hash=?", (_hash(token),))
    if not row or row["revoked_at"]:
        raise SigninEngineError("签到码已失效，请扫描现场最新二维码", 410)
    expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires <= datetime.now(UTC):
        raise SigninEngineError("签到码已过有效期", 410)
    return str(row["event_id"])


def wechat_code(token: str, env_version: str) -> tuple[bytes, str]:
    try:
        access_token = _wechat_access_token()
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.post("https://api.weixin.qq.com/wxa/getwxacodeunlimit",
                                   params={"access_token": access_token},
                                   json={"scene": token, "page": PAGE,
                                         "env_version": env_version,
                                         "check_path": env_version == "release", "width": 430})
        mime = response.headers.get("content-type", "").split(";", 1)[0].strip()
        if response.status_code != 200 or mime not in {"image/png", "image/jpeg"}:
            raise SigninEngineError("小程序码生成失败，请确认对应小程序版本已包含签到页面", 502)
        if not response.content or len(response.content) > 2_000_000:
            raise SigninEngineError("小程序码返回格式无效", 502)
        return response.content, mime
    except (httpx.HTTPError, WeChatProviderError) as exc:
        raise SigninEngineError("微信小程序码服务暂时不可用", 502) from exc


def generate_entry(event_id: str, *, user: dict, env_version: str = "develop") -> dict:
    enabled(write=True)
    event = event_detail(event_id, user=user, permission="attendance:code")
    # Route tokens last through the activity date, including evening sessions.
    event_date = str(event.get("event_date") or "")[:10]
    try:
        from zoneinfo import ZoneInfo
        expires = datetime.fromisoformat(event_date).replace(tzinfo=ZoneInfo("Asia/Shanghai")) + timedelta(days=2)
    except ValueError as exc:
        raise SigninEngineError("活动日期未确认，无法生成签到码", 400) from exc
    if expires <= datetime.now(UTC):
        raise SigninEngineError("活动已结束，不能生成新的现场签到码", 409)
    token = secrets.token_urlsafe(16)
    content, mime = wechat_code(token, env_version)
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    expires_at = expires.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    with transaction() as connection:
        execute(connection, "UPDATE attendance_entry_tokens SET revoked_at=? WHERE event_id=? AND revoked_at IS NULL", (now, event_id))
        execute(connection, "INSERT INTO attendance_entry_tokens(token_hash,event_id,created_by,created_at,expires_at) VALUES (?,?,?,?,?)", (_hash(token), event_id, user["id"], now, expires_at))
        write_audit(connection, actor_user_id=user["id"], action="attendance.code.regenerate",
                    resource_type="signin_event", resource_id=event_id,
                    org_unit_id=event.get("org_unit_id") or event.get("class_org_unit_id"),
                    before={"active_codes": "revoked"},
                    after={"page": PAGE, "env_version": env_version, "expires_at": expires_at})
    return {"event_id": event_id, "page": PAGE, "env_version": env_version,
            "token": token, "image_base64": base64.b64encode(content).decode(),
            "mime_type": mime, "expires_at": expires_at}
