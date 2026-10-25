from __future__ import annotations

from typing import Literal
from fastapi import APIRouter, Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.services import wechat_operations_v2 as service, mobile_activities
from app.api.enrollment import EnrollmentReviewPayload, EnrollmentPaymentPayload, EnrollmentRejectPayload

router = APIRouter(prefix="/operations")
bearer = HTTPBearer(auto_error=False)


def session(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    from app.api.wechat import _operation_session
    return _operation_session(credentials)


def response(function, identity, *args, **kwargs):
    from app.api.wechat import _operation_call
    return {"success": True, "data": _operation_call(function, identity, *args, **kwargs)}


class RenewalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["RENEWED", "NOT_RENEWING", "IN_COMMUNICATION", "DEFERRED"]
    expected_status: str = Field(min_length=1, max_length=64)


class MeetingVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class ActivitySave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    event_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    org_unit_id: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=8, max_length=64)
    expected_revision: str | None = Field(default=None, max_length=64)
    checkin_start: str = Field(default="08:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    checkin_end: str = Field(default="22:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class AttendeeAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=255)
    phone: str | None = Field(default=None, pattern=r"^$|^1\d{10}$")
    request_id: str = Field(min_length=8, max_length=64)


class ActivityLifecycle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["DRAFT", "CONFIRMED", "CANCELLED"]


class ApplicationRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_updated_at: str = Field(min_length=1, max_length=64)


class ApplicationReview(EnrollmentReviewPayload, ApplicationRevision):
    pass


class ApplicationPayment(EnrollmentPaymentPayload, ApplicationRevision):
    pass


class ApplicationReject(EnrollmentRejectPayload, ApplicationRevision):
    pass


@router.get("/members/{member_id}/birthday-copy")
def birthday_copy(member_id: int, identity=Depends(session)):
    return response(service.birthday_copy, identity, member_id)


@router.get("/renewals/{cycle_id}")
def renewal(cycle_id: int, identity=Depends(session)):
    return response(service.renewal_detail, identity, cycle_id)


@router.patch("/renewals/{cycle_id}")
def renewal_update(cycle_id: int, payload: RenewalUpdate, identity=Depends(session)):
    return response(service.renewal_update, identity, cycle_id, **payload.model_dump())


@router.get("/study-meeting-records")
def meetings(month: str = Query(pattern=r"^\d{4}-\d{2}$"), identity=Depends(session)):
    return response(service.meetings, identity, month)


@router.get("/study-meeting-records/{session_id}")
def meeting(session_id: int, identity=Depends(session)):
    return response(service.meeting_detail, identity, session_id)


@router.post("/study-meeting-records/{session_id}/verify-settle")
def verify(session_id: int, payload: MeetingVerify, identity=Depends(session)):
    return response(service.verify_meeting, identity, session_id, payload.expected_revision)


@router.get("/study-meeting-records/{session_id}/evidence")
def evidence(session_id: int, identity=Depends(session)):
    from fastapi.responses import Response
    from app.services.study_meeting_evidence import read_evidence
    from app.api.wechat import _operation_call
    def read(current):
        principal = service._principal(current)
        return service.call(principal, "plans:read", read_evidence, actor_user_id=int(principal["user_id"]), session_id=session_id)
    content, content_type = _operation_call(read, identity)
    return Response(content, media_type=content_type, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/study-meeting-records/{session_id}/evidence-preview")
def evidence_preview(session_id: int, identity=Depends(session)):
    # A small JSON preview works with both wx.request and cloud.callContainer.
    import base64
    import io
    from PIL import Image, ImageOps
    original = evidence(session_id, identity)
    with Image.open(io.BytesIO(original.body)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((1280, 1280))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=80)
    from fastapi.responses import JSONResponse
    return JSONResponse({"success": True, "data": {"base64": base64.b64encode(output.getvalue()).decode("ascii"), "content_type": "image/jpeg"}}, headers={"Cache-Control": "private, no-store"})


@router.get("/dashboard")
def dashboard(year: int = Query(ge=2020, le=2100), month: int = Query(ge=1, le=12), identity=Depends(session)):
    return response(service.dashboard, identity, year, month)


@router.get("/applications")
def applications(query: str | None = Query(default=None, max_length=255),
                 application_status: Literal["SUBMITTED", "APPROVED", "REJECTED", "ENROLLED", "CANCELLED"] | None = None,
                 identity=Depends(session)):
    return response(service.applications, identity, query, application_status)


@router.get("/applications/{application_id}")
def application(application_id: int, identity=Depends(session)):
    return response(service.application_detail, identity, application_id)


@router.patch("/applications/{application_id}/review")
def review(application_id: int, payload: ApplicationReview, identity=Depends(session)):
    values = payload.model_dump(exclude_unset=True)
    decision = values.pop("decision", payload.decision)
    note = values.pop("review_note", None)
    revision = values.pop("expected_updated_at")
    return response(service.application_action, identity, application_id, "review", {"decision": decision, "updates": values, "note": note}, revision)


@router.get("/applications/{application_id}/image-summary")
def application_image(application_id: int, identity=Depends(session)):
    return response(service.application_image, identity, application_id)


@router.post("/applications/{application_id}/payment")
def payment(application_id: int, payload: ApplicationPayment, identity=Depends(session)):
    if payload.payment_status != "PAID":
        from fastapi import HTTPException
        raise HTTPException(400, "仅开放已收款确认")
    return response(service.application_action, identity, application_id, "payment", payload.model_dump(), payload.expected_updated_at)


@router.post("/applications/{application_id}/reject")
def reject(application_id: int, payload: ApplicationReject, identity=Depends(session)):
    return response(service.application_action, identity, application_id, "reject", payload.model_dump(), payload.expected_updated_at)


@router.post("/applications/{application_id}/enroll")
def enroll(application_id: int, payload: ApplicationRevision, identity=Depends(session)):
    return response(service.application_action, identity, application_id, "enroll", {}, payload.expected_updated_at)


@router.get("/activity-context")
def activity_context(identity=Depends(session)):
    return response(mobile_activities.context, identity)


@router.get("/activities")
def activities(month: str = Query(pattern=r"^\d{4}-\d{2}$"), page: int = Query(default=1, ge=1, le=1000), identity=Depends(session)):
    return response(mobile_activities.list_activities, identity, month, page)


@router.post("/activities")
def create_activity(payload: ActivitySave, identity=Depends(session)):
    return response(mobile_activities.save, identity, payload.model_dump())


@router.get("/activities/{event_id}")
def activity(event_id: str, identity=Depends(session)):
    return response(mobile_activities.detail, identity, event_id)


@router.patch("/activities/{event_id}")
def update_activity(event_id: str, payload: ActivitySave, identity=Depends(session)):
    return response(mobile_activities.save, identity, payload.model_dump(), event_id)


@router.post("/activities/{event_id}/attendees")
def add_attendee(event_id: str, payload: AttendeeAdd, identity=Depends(session)):
    return response(mobile_activities.add_attendee, identity, event_id, payload.model_dump())


@router.post("/activities/{event_id}/lifecycle")
def activity_lifecycle(event_id: str, payload: ActivityLifecycle, identity=Depends(session)):
    return response(mobile_activities.lifecycle, identity, event_id, payload.status)
