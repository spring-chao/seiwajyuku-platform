"""Native member check-in uses a verified WeChat identity, never a posted name."""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.core.settings import get_settings
from app.db import fetch_one
from app.services import signin_engine
from app.services.attendance_entry_codes import resolve_entry
from app.services.wechat_identity import (
    resolve_wechat_session, exchange_wechat_code, resume_wechat_binding,
    WeChatIdentityError, WeChatProviderError,
)
from app.services.wechat_learning import _learning_type_name, LEARNING_CATEGORIES
from app.services.native_checkin_ticket import create_ticket


router = APIRouter(prefix="/api/v1/wechat/checkin", tags=["wechat-checkin"])
bearer = HTTPBearer(auto_error=False)


class ConfirmPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=128)
    token: str | None = Field(default=None, min_length=16, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")


class EntryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    token: str | None = Field(default=None, min_length=16, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    wx_login_code: str = Field(min_length=1, max_length=512)


class GuestConfirmPayload(EntryPayload):
    name: str = Field(min_length=1, max_length=120)


def _raise(exc: signin_engine.SigninEngineError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


def _member(credentials: HTTPAuthorizationCredentials | None, *, required: bool = True) -> dict | None:
    if not credentials:
        if not required:
            return None
        raise HTTPException(401, "请先绑定学员身份")
    try:
        session = resolve_wechat_session(credentials.credentials)
    except WeChatIdentityError as exc:
        raise HTTPException(401, str(exc)) from exc
    if not session.get("member") and not required:
        return None
    row = fetch_one("SELECT id, member_code, name FROM members WHERE id=? AND status='ACTIVE'", (session["member_id"],))
    if not row:
        raise HTTPException(401, "当前学员身份已失效")
    return {"member_id": int(row["id"]), "member_code": row["member_code"], "name": row["name"],
            "binding_id": session["binding_id"], "token_version": session["token_version"],
            "class_org_unit_id": session["member"].get("class_org_unit_id"),
            "home_class_org_unit_id": session["member"].get("class_org_unit_id"),
            "class_name": session["member"].get("class_name") or "",
            "group_org_unit_id": session["member"].get("study_group_org_unit_id"),
            "group_name": session["member"].get("study_group_name") or ""}


def _event_id(event_id: str | None, token: str | None) -> str:
    resolved = resolve_entry(token) if token else None
    if resolved and event_id and resolved != event_id:
        raise signin_engine.SigninEngineError("签到码与当前活动不一致", 400)
    result = resolved or event_id
    if not result:
        raise signin_engine.SigninEngineError("请选择本次签到活动", 400)
    return result


def _event(raw: dict) -> dict:
    event = dict(raw)
    event_id = str(raw.get("event_id") or raw.get("id") or raw.get("_id") or "")
    event.update({"event_id": event_id,
                  "title": raw.get("title") or raw.get("name") or raw.get("event_name") or "",
                  "session_name": raw.get("session_name") or {"MORNING": "上午", "AFTERNOON": "下午", "KONPA": "晚上空巴"}.get(raw.get("session_code"), "本次活动"),
                  "checkin_start_at": raw.get("checkin_start_at") or raw.get("checkin_open_at"),
                  "checkin_end_at": raw.get("checkin_end_at") or raw.get("checkin_close_at")})
    return signin_engine.redact(event)


def _fallback(event_id: str) -> str | None:
    url = get_settings().signin_legacy_url
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return None
    query = dict(parse_qsl(parsed.query))
    query["event_id"] = event_id
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _lookup(event_id: str, member: dict | None) -> dict:
    result = signin_engine.engine_request("/ops/v1/member-checkin/lookup", {"event_id": event_id, "member": member})
    event = _event(result.get("event") or (result.get("data") or {}).get("event") or {})
    if not event["event_id"]:
        raise signin_engine.SigninEngineError("活动信息未确认，无法签到", 502)
    return {**result, "event": event}


def _kind(event: dict) -> str:
    category = _learning_type_name(event.get("activity_type"), event.get("title"))
    return "learning" if category in LEARNING_CATEGORIES else "activity"


def _receipt(raw: dict | None) -> dict:
    """Preserve the original success display, with only attendee-facing fields."""
    raw = raw if isinstance(raw, dict) else {}
    fields = ("name", "group_num", "dinner_table_num", "show_group", "show_dinner_table",
              "attendance_role", "home_class_name", "registered_name", "group_type", "group_value",
              "class_name", "center", "group_name", "company", "multi_total", "multi_checked", "checked_at")
    receipt = {key: raw[key] for key in fields if key in raw and isinstance(raw[key], (str, int, float, bool, type(None)))}
    event = raw.get("event")
    if isinstance(event, dict):
        receipt["event"] = {key: event[key] for key in ("event_id", "name", "title", "event_date", "activity_type_name")
                            if key in event and isinstance(event[key], (str, int))}
    return receipt


@router.get("/events")
def events(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    try:
        signin_engine.enabled(member=True)
        member = _member(credentials)
        result = signin_engine.engine_request("/ops/v1/member-checkin/events", {"member": member})
        items = result.get("events") or result.get("items") or []
        return {"success": True, "data": {"events": [_event(item) for item in items]}}
    except signin_engine.SigninEngineError as exc:
        raise _raise(exc) from exc


@router.get("/context")
def context(event_id: str | None = Query(default=None, min_length=1, max_length=128),
            token: str | None = Query(default=None, min_length=16, max_length=32, pattern=r"^[A-Za-z0-9_-]+$"),
            credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    try:
        signin_engine.enabled(member=True)
        selected_id = _event_id(event_id, token)
        member = _member(credentials, required=False)
        lookup = _lookup(selected_id, member)
        event = lookup["event"]
        registration = lookup.get("registration") if member else None
        already = bool(lookup.get("already_checked_in") or lookup.get("already") or
                       isinstance(registration, dict) and registration.get("checked_in"))
        ticket = None
        confirm_url = None
        if member and (lookup.get("can_checkin", registration is not None) or already):
            signin_engine.enabled(member=True, write=True)
            ticket_member = {key: value for key, value in member.items() if key not in {"binding_id", "token_version"}}
            ticket = create_ticket(selected_id, ticket_member,
                                   binding_id=member["binding_id"], token_version=member["token_version"])
            base = get_settings().signin_api_base_url.rstrip("/")
            if urlsplit(base).scheme == "https":
                confirm_url = base + "/native/v1/checkin/confirm"
        return {"success": True, "data": {
            "event": event, "member": {key: value for key, value in member.items() if key not in {"binding_id", "token_version"}} if member else None,
            "registration": signin_engine.redact(registration),
            "already_checked_in": already,
            "receipt": _receipt(lookup.get("receipt")) if member and already else None,
            "can_checkin": bool(member and lookup.get("can_checkin", registration is not None) and not already),
            "requires_fallback": bool(lookup.get("requires_fallback")),
            "notice": (lookup.get("notice") or lookup.get("msg") or "") if member else "请输入您的姓名，确认本场活动签到。",
            "guest_allowed": member is None,
            "fallback_url": _fallback(selected_id), "history_kind": _kind(event),
            "checkin_ticket": ticket, "engine_confirm_url": confirm_url,
        }}
    except signin_engine.SigninEngineError as exc:
        raise _raise(exc) from exc


@router.post("/entry")
def entry(payload: EntryPayload) -> dict:
    """Scanning a code restores an existing binding without another form."""
    try:
        signin_engine.enabled(member=True)
        selected_id = _event_id(payload.event_id, payload.token)
        identity = exchange_wechat_code(payload.wx_login_code)
        access_token = resume_wechat_binding(identity)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=access_token) if access_token else None
        result = context(event_id=selected_id, token=payload.token, credentials=credentials)
        if access_token:
            result["data"]["access_token"] = access_token
        return result
    except WeChatProviderError as exc:
        raise HTTPException(503, str(exc)) from exc
    except WeChatIdentityError as exc:
        raise HTTPException(401, str(exc)) from exc
    except signin_engine.SigninEngineError as exc:
        raise _raise(exc) from exc


@router.post("/guest-confirm")
def guest_confirm(payload: GuestConfirmPayload) -> dict:
    try:
        signin_engine.enabled(member=True, write=True)
        selected_id = _event_id(payload.event_id, payload.token)
        name = payload.name.strip()
        if not name or any(ord(char) < 32 or ord(char) == 127 for char in name):
            raise HTTPException(400, "请填写有效姓名")
        identity = exchange_wechat_code(payload.wx_login_code)
        access_token = resume_wechat_binding(identity)
        if access_token and _member(HTTPAuthorizationCredentials(scheme="Bearer", credentials=access_token), required=False):
            raise HTTPException(409, "当前微信已绑定塾生，请重新打开活动页确认本人签到")
        # Name is descriptive only; separate WeChat accounts with the same name
        # get distinct guest facts. Raw openid never leaves this service.
        guest_id = hmac.new(get_settings().signin_platform_api_key.encode(),
                            ("signin-guest\x1f" + str(identity["appid"]) + "\x1f" + str(identity["openid"])).encode(),
                            hashlib.sha256).hexdigest()
        result = signin_engine.engine_request("/ops/v1/guest-checkin/confirm", {
            "event_id": selected_id, "guest_id": guest_id, "name": name,
        }, timeout=12)
        checked_at = result.get("checked_at") or (result.get("data") or {}).get("checked_at")
        if not checked_at:
            raise signin_engine.SigninEngineError("尚未确认实际签到记录，请重试", 502)
        return {"success": True, "data": {
            "status": "ALREADY_CHECKED_IN" if result.get("already") is True else "CHECKED_IN",
            "participant_type": "GUEST", "checked_at": checked_at,
            "receipt": _receipt(result.get("data")),
            "message": result.get("msg") or "签到成功",
            "sync_status": "SYNCED" if result.get("sync_status") == "SYNCED" else "PENDING",
        }}
    except WeChatProviderError as exc:
        raise HTTPException(503, str(exc)) from exc
    except WeChatIdentityError as exc:
        raise HTTPException(401, str(exc)) from exc
    except signin_engine.SigninEngineError as exc:
        raise _raise(exc) from exc


@router.post("/confirm")
def confirm(payload: ConfirmPayload,
            credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    try:
        signin_engine.enabled(member=True, write=True)
        member = _member(credentials)
        selected_id = _event_id(payload.event_id, payload.token)
        lookup = _lookup(selected_id, member)
        registration = lookup.get("registration") or {}
        if lookup.get("requires_fallback"):
            raise signin_engine.SigninEngineError("当前报名需要现场工作人员确认，请使用备用签到入口", 409)
        registration_id = registration.get("registration_id") or registration.get("id") or registration.get("_id")
        if not registration_id and not lookup.get("cross_class_member"):
            raise signin_engine.SigninEngineError("未找到本人报名记录，请联系现场工作人员", 409)
        result = signin_engine.engine_request("/ops/v1/member-checkin/confirm", {
            "event_id": selected_id, "member": member,
            "registration_id": registration_id,
        }, timeout=12)
        data = result.get("data") or {}
        if result.get("requires_fallback") or result.get("status") in {"NOT_REGISTERED", "TEAM_FALLBACK", "IDENTITY_CONFLICT"}:
            raise signin_engine.SigninEngineError("当前报名需要备用签到或现场工作人员确认", 409)
        already = result.get("already") is True
        checked_at = result.get("checked_at") or data.get("checked_at")
        if not already and not checked_at:
            raise signin_engine.SigninEngineError("尚未确认实际签到记录，请重试", 502)
        event = lookup["event"]
        sync_status = "SYNCED" if result.get("sync_status") == "SYNCED" else "PENDING"
        return {"success": True, "data": {
            "status": "ALREADY_CHECKED_IN" if already else "CHECKED_IN",
            "checked_at": checked_at,
            "receipt": _receipt(data),
            "sync_status": sync_status, "message": result.get("msg") or ("已签到" if already else "签到成功"),
            "event": event, "history_kind": _kind(event),
        }}
    except signin_engine.SigninEngineError as exc:
        raise _raise(exc) from exc
