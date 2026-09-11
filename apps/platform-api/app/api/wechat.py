from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.core.settings import get_settings
from app.services.wechat_identity import (
    WeChatIdentityError,
    WeChatProviderError,
    get_wechat_identity_context,
    resolve_wechat_session,
    resolve_member_session,
    revoke_member_binding,
    verify_person_binding,
    verify_staff_binding,
    verify_member_binding,
)
from app.services import wechat_operations
from app.services.volunteer_positions import (
    get_member_volunteer_history,
    get_member_volunteer_services,
)
from app.services.wechat_learning import get_member_learning_summary


router = APIRouter(prefix="/api/v1/wechat", tags=["wechat-identity"])
bearer = HTTPBearer(auto_error=False)


class MemberBindingVerifyPayload(BaseModel):
    code: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=6, max_length=32)


class StaffBindingVerifyPayload(BaseModel):
    code: str = Field(min_length=1, max_length=512)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class PersonBindingVerifyPayload(BaseModel):
    """Unified natural-person binding payload for the current mini-program."""

    model_config = ConfigDict(extra="forbid")

    wx_login_code: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=6, max_length=32)
    # Optional for member-only identities; mandatory and verified against the
    # staff profile when the resolved person has an effective staff identity.
    phone_verification: str | None = Field(default=None, max_length=512)


class FollowupRecordPayload(BaseModel):
    channel: str = Field(min_length=2, max_length=32)
    contacted_at: str = Field(min_length=10, max_length=64)
    outcome_code: str = Field(min_length=2, max_length=32)
    subject_statement: str | None = Field(default=None, max_length=2000)
    objective_facts: str | None = Field(default=None, max_length=4000)
    staff_judgment: str | None = Field(default=None, max_length=2000)
    next_action: str | None = Field(default=None, max_length=2000)
    next_followup_at: str | None = Field(default=None, max_length=64)


def _ensure_enabled() -> None:
    if not get_settings().wechat_member_binding_enabled:
        raise HTTPException(404, "微信学员身份功能尚未开启")


def _session_token(
    credentials: HTTPAuthorizationCredentials | None,
) -> str:
    _ensure_enabled()
    if not credentials:
        raise HTTPException(401, "需要绑定微信身份")
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


@router.post("/person-bindings/verify")
def verify_person_identity(payload: PersonBindingVerifyPayload) -> dict:
    """Bind one WeChat user to all currently effective identities of a person."""

    _ensure_enabled()
    try:
        data = verify_person_binding(
            wx_login_code=payload.wx_login_code,
            name=payload.name,
            phone=payload.phone,
            phone_verification=payload.phone_verification,
        )
    except WeChatProviderError as exc:
        raise HTTPException(503, str(exc)) from exc
    except WeChatIdentityError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/staff-bindings/verify")
def verify_staff_identity(payload: StaffBindingVerifyPayload) -> dict:
    _ensure_enabled()
    try:
        data = verify_staff_binding(
            code=payload.code,
            username=payload.username,
            password=payload.password,
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
        data = resolve_wechat_session(token)
    except WeChatIdentityError as exc:
        raise HTTPException(401, str(exc)) from exc
    # The provider credential and binding id remain server-side details.
    return {
        "success": True,
        "data": {
            "member": data["member"],
            "identities": get_wechat_identity_context(data),
        },
    }


@router.get("/learning-summary")
def learning_summary(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    """Return learning facts for the member resolved by the WeChat session."""

    token = _session_token(credentials)
    try:
        session = resolve_member_session(token)
        data = get_member_learning_summary(session["member_id"])
    except WeChatIdentityError as exc:
        raise HTTPException(401, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {"success": True, "data": data}


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


@router.get("/volunteer-history")
def volunteer_history(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    """Return the current member's privacy-safe formal volunteer history."""

    token = _session_token(credentials)
    try:
        session = resolve_member_session(token)
        data = get_member_volunteer_history(session["member_id"])
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


def _operation_session(
    credentials: HTTPAuthorizationCredentials | None,
) -> dict:
    token = _session_token(credentials)
    try:
        return resolve_wechat_session(token)
    except WeChatIdentityError as exc:
        raise HTTPException(401, str(exc)) from exc


def _operation_call(function, session: dict, *args, **kwargs):
    try:
        return function(session, *args, **kwargs)
    except WeChatIdentityError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/operations/workbench")
def operations_workbench(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    return {"success": True, "data": _operation_call(wechat_operations.operation_workbench, _operation_session(credentials))}


@router.get("/operations/today-actions")
def operations_today_actions(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    return {"success": True, "data": _operation_call(wechat_operations.today_actions, _operation_session(credentials))}


@router.get("/operations/member-search")
def operations_member_search(
    name: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    return {
        "success": True,
        "data": _operation_call(
            wechat_operations.member_search,
            _operation_session(credentials),
            name=name,
            limit=limit,
        ),
    }


@router.get("/operations/followup-tasks")
def operations_followup_tasks(
    status: str | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    return {
        "success": True,
        "data": _operation_call(
            wechat_operations.followup_tasks,
            _operation_session(credentials),
            status=status,
        ),
    }


@router.post("/operations/followup-tasks/{task_id}/records")
def operations_record_followup(
    task_id: int,
    payload: FollowupRecordPayload,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    return {
        "success": True,
        "data": _operation_call(
            wechat_operations.record_followup,
            _operation_session(credentials),
            task_id=task_id,
            **payload.model_dump(),
        ),
    }


@router.get("/operations/study-meetings")
def operations_study_meetings(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    return {
        "success": True,
        "data": _operation_call(
            wechat_operations.study_meeting_records,
            _operation_session(credentials),
        ),
    }
