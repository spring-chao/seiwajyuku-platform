from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.core.settings import get_settings
from app.services.study_meetings import (
    StudyMeetingError,
    StudyMeetingFeatureDisabled,
    StudyMeetingPermissionError,
    create_study_meeting,
    get_study_meeting,
    get_study_meeting_context,
    member_from_session_token,
    search_cross_group_members,
    submit_study_meeting,
    get_study_meeting_record_for_operations,
    list_study_meeting_records,
)


router = APIRouter(prefix="/api/v1/study-meetings", tags=["study-meetings"])
bearer = HTTPBearer(auto_error=False)


class StudyMeetingCreatePayload(BaseModel):
    group_org_unit_id: str = Field(min_length=1, max_length=64)
    meeting_date: str | None = Field(default=None, max_length=32)
    member_ids: list[int] = Field(default_factory=list, max_length=500)
    cross_group_member_ids: list[int] = Field(default_factory=list, max_length=500)
    has_course: bool = False
    course_key: str | None = Field(default=None, max_length=128)


def _member_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    if not credentials:
        if not get_settings().study_meeting_submission_enabled:
            raise HTTPException(404, "小组学习会登记功能尚未开启")
        raise HTTPException(401, "需要绑定微信学员身份")
    try:
        return member_from_session_token(credentials.credentials)
    except StudyMeetingPermissionError as exc:
        raise HTTPException(401, str(exc)) from exc


def _business_error(exc: Exception) -> HTTPException:
    if isinstance(exc, StudyMeetingFeatureDisabled):
        return HTTPException(404, str(exc))
    if isinstance(exc, StudyMeetingPermissionError):
        return HTTPException(403, str(exc))
    return HTTPException(400, str(exc))


@router.get("/context")
def study_meeting_context(
    group_org_unit_id: str | None = Query(default=None, max_length=64),
    session: dict = Depends(_member_session),
) -> dict:
    try:
        data = get_study_meeting_context(
            member_id=session["member_id"], group_org_unit_id=group_org_unit_id
        )
    except (StudyMeetingError, StudyMeetingPermissionError) as exc:
        raise _business_error(exc) from exc
    data["member"] = session["member"]
    return {"success": True, "data": data}


@router.get("/cross-group-members")
def cross_group_members(
    group_org_unit_id: str = Query(min_length=1, max_length=64),
    q: str | None = Query(default=None, max_length=80),
    session: dict = Depends(_member_session),
) -> dict:
    try:
        data = search_cross_group_members(
            member_id=session["member_id"], group_org_unit_id=group_org_unit_id, query=q
        )
    except (StudyMeetingError, StudyMeetingPermissionError) as exc:
        raise _business_error(exc) from exc
    return {"success": True, "data": {"members": data}}


@router.post("")
def create_meeting(
    payload: StudyMeetingCreatePayload,
    session: dict = Depends(_member_session),
) -> dict:
    try:
        data = create_study_meeting(
            member_id=session["member_id"],
            group_org_unit_id=payload.group_org_unit_id,
            meeting_date=payload.meeting_date,
            member_ids=payload.member_ids,
            cross_group_member_ids=payload.cross_group_member_ids,
            has_course=payload.has_course,
            course_key=payload.course_key,
        )
    except (StudyMeetingError, StudyMeetingPermissionError) as exc:
        raise _business_error(exc) from exc
    return {"success": True, "data": data}


@router.get("/records")
def operations_records(
    status: str | None = Query(default=None, max_length=16),
    meeting_date_from: str | None = Query(default=None, max_length=32),
    meeting_date_to: str | None = Query(default=None, max_length=32),
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    try:
        data = list_study_meeting_records(
            actor_user_id=user["id"],
            status=status,
            meeting_date_from=meeting_date_from,
            meeting_date_to=meeting_date_to,
        )
    except (StudyMeetingError, StudyMeetingPermissionError) as exc:
        raise _business_error(exc) from exc
    return {"success": True, "data": {"records": data}}


@router.get("/records/{session_id}")
def operations_record_detail(
    session_id: int,
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    try:
        data = get_study_meeting_record_for_operations(
            actor_user_id=user["id"], session_id=session_id
        )
    except (StudyMeetingError, StudyMeetingPermissionError) as exc:
        raise _business_error(exc) from exc
    return {"success": True, "data": data}


@router.post("/{session_id}/submit")
def submit_meeting(
    session_id: int,
    session: dict = Depends(_member_session),
) -> dict:
    try:
        data = submit_study_meeting(
            member_id=session["member_id"], session_id=session_id
        )
    except (StudyMeetingError, StudyMeetingPermissionError) as exc:
        raise _business_error(exc) from exc
    return {"success": True, "data": data}


@router.get("/{session_id}")
def meeting_detail(
    session_id: int,
    session: dict = Depends(_member_session),
) -> dict:
    try:
        data = get_study_meeting(
            member_id=session["member_id"], session_id=session_id
        )
    except (StudyMeetingError, StudyMeetingPermissionError) as exc:
        raise _business_error(exc) from exc
    return {"success": True, "data": data}
