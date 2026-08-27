from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StrictInt

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
    correct_meeting_courses,
)
from app.services.study_meeting_evidence import upload_evidence, read_evidence
from app.services.study_evidence_storage import MAX_BYTES
from app.services.study_meeting_attendees import attendee_options, correct_attendees


router = APIRouter(prefix="/api/v1/study-meetings", tags=["study-meetings"])
bearer = HTTPBearer(auto_error=False)


class StudyMeetingCreatePayload(BaseModel):
    group_org_unit_id: str = Field(min_length=1, max_length=64)
    meeting_date: str | None = Field(default=None, max_length=32)
    member_ids: list[int] = Field(default_factory=list, max_length=500)
    cross_group_member_ids: list[int] = Field(default_factory=list, max_length=500)
    has_course: bool = False
    course_key: str | None = Field(default=None, max_length=128)
    course_keys: list[str] | None = Field(default=None, max_length=500)


class CourseCorrectionPayload(BaseModel):
    course_keys: list[str] = Field(max_length=500)
    expected_course_keys: list[str] = Field(max_length=500)
    note: str | None = Field(default=None, max_length=1000)


class AttendeeCorrectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_ids: list[StrictInt] = Field(min_length=1, max_length=500)
    expected_member_ids: list[StrictInt] = Field(max_length=500)
    note: str | None = Field(default=None, max_length=1000)


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
            course_keys=payload.course_keys,
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


@router.patch("/records/{session_id}/courses")
def correct_courses(session_id: int, payload: CourseCorrectionPayload,
                    user: dict = Depends(require_permission("study_meetings:courses_edit"))) -> dict:
    try:
        data = correct_meeting_courses(actor_user_id=user["id"], session_id=session_id,
                                       **payload.model_dump())
        return {"success": True, "data": data}
    except (StudyMeetingError, StudyMeetingPermissionError) as exc:
        raise _business_error(exc) from exc


@router.get("/records/{session_id}/attendee-options")
def operation_attendee_options(session_id: int,
                              user: dict = Depends(require_permission("study_meetings:attendees_edit"))) -> dict:
    try:
        return {"success": True, "data": attendee_options(actor_user_id=user["id"], session_id=session_id)}
    except (StudyMeetingError, StudyMeetingPermissionError) as exc:
        raise _business_error(exc) from exc


@router.patch("/records/{session_id}/attendees")
def correct_record_attendees(session_id: int, payload: AttendeeCorrectionPayload,
                             user: dict = Depends(require_permission("study_meetings:attendees_edit"))) -> dict:
    try:
        return {"success": True, "data": correct_attendees(actor_user_id=user["id"], session_id=session_id,
                                                           **payload.model_dump())}
    except (StudyMeetingError, StudyMeetingPermissionError) as exc:
        raise _business_error(exc) from exc


def _photo_response(session_id: int, **identity) -> Response:
    try:
        content, content_type = read_evidence(session_id=session_id, **identity)
        return Response(content, media_type=content_type, headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'inline; filename="study-photo"',
        })
    except (StudyMeetingError, StudyMeetingPermissionError) as exc:
        raise _business_error(exc) from exc


@router.get("/records/{session_id}/evidence")
def operations_evidence(session_id: int, user: dict = Depends(require_permission("plans:read"))) -> Response:
    return _photo_response(session_id, actor_user_id=user["id"])


@router.get("/{session_id}/evidence")
def member_evidence(session_id: int, session: dict = Depends(_member_session)) -> Response:
    return _photo_response(session_id, member_id=session["member_id"])


@router.post("/{session_id}/evidence")
def save_evidence(session_id: int, photo: UploadFile = File(...),
                  session: dict = Depends(_member_session)) -> dict:
    try:
        content = photo.file.read(MAX_BYTES + 1)
        data = upload_evidence(member_id=session["member_id"], session_id=session_id,
                               content=content, content_type=photo.content_type or "")
        return {"success": True, "data": data}
    except (StudyMeetingError, StudyMeetingPermissionError) as exc:
        raise _business_error(exc) from exc
    finally:
        photo.file.close()


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
