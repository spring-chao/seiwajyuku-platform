from __future__ import annotations

import ipaddress
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import require_permission
from app.services.enrollment import (
    EnrollmentRateLimitError,
    confirm_enrollment_payment,
    create_enrollment_link,
    disable_enrollment_link,
    enroll_application,
    generate_wechat_miniprogram_code,
    get_active_enrollment_link,
    get_enrollment_application,
    get_public_enrollment_form,
    list_enrollment_applications,
    reject_enrollment_application,
    review_enrollment_application,
    rotate_enrollment_link,
    submit_public_enrollment,
)


router = APIRouter(prefix="/api/v1", tags=["member-enrollment"])


class PublicEnrollmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    phone: str = Field(pattern=r"^1\d{10}$")
    privacy_consent: Literal[True]
    gender: Literal["MALE", "FEMALE", "OTHER"] | None = None
    birthday: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    district: str | None = Field(default=None, max_length=255)
    political_status: str | None = Field(default=None, max_length=255)
    company_name: str = Field(min_length=1, max_length=500)
    company_address: str = Field(min_length=1, max_length=1000)
    email: str | None = Field(default=None, max_length=255)
    position: str = Field(min_length=1, max_length=255)
    referrer: str = Field(min_length=1, max_length=255)
    invoice_info: str = Field(min_length=1, max_length=4000)
    invoice_type: str = Field(min_length=1, max_length=64)
    industry_category: str | None = Field(default=None, max_length=255)
    industry: str = Field(min_length=1, max_length=255)
    company_products: str = Field(min_length=1, max_length=4000)
    employee_count: int = Field(ge=0, le=10000000)
    books_read: str = Field(min_length=1, max_length=4000)
    enrollment_reason_philosophy: str = Field(min_length=1, max_length=4000)
    enrollment_reason_change: str = Field(min_length=1, max_length=4000)
    enrollment_reason_other: str = Field(min_length=1, max_length=4000)
    learning_years_goal: str | None = Field(default=None, max_length=255)
    learning_participation_goal: str | None = Field(default=None, max_length=4000)
    business_goal: str | None = Field(default=None, max_length=4000)
    other_goal: str | None = Field(default=None, max_length=4000)
    annual_sales: str = Field(min_length=1, max_length=255)
    profit_margin: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=1000)


class EnrollmentReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["SAVE", "APPROVE"] = "APPROVE"
    review_note: str | None = Field(default=None, max_length=2000)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    gender: Literal["MALE", "FEMALE", "OTHER"] | None = None
    birthday: str | None = Field(default=None, pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    district: str | None = Field(default=None, max_length=255)
    political_status: str | None = Field(default=None, max_length=255)
    company_name: str | None = Field(default=None, max_length=500)
    company_address: str | None = Field(default=None, max_length=1000)
    email: str | None = Field(default=None, max_length=255)
    position: str | None = Field(default=None, max_length=255)
    referrer: str | None = Field(default=None, max_length=255)
    invoice_info: str | None = Field(default=None, max_length=4000)
    invoice_type: str | None = Field(default=None, max_length=64)
    industry_category: str | None = Field(default=None, max_length=255)
    industry: str | None = Field(default=None, max_length=255)
    company_products: str | None = Field(default=None, max_length=4000)
    employee_count: int | None = Field(default=None, ge=0, le=10000000)
    books_read: str | None = Field(default=None, max_length=4000)
    enrollment_reason_philosophy: str | None = Field(default=None, max_length=4000)
    enrollment_reason_change: str | None = Field(default=None, max_length=4000)
    enrollment_reason_other: str | None = Field(default=None, max_length=4000)
    learning_years_goal: str | None = Field(default=None, max_length=255)
    learning_participation_goal: str | None = Field(default=None, max_length=4000)
    business_goal: str | None = Field(default=None, max_length=4000)
    other_goal: str | None = Field(default=None, max_length=4000)
    annual_sales: str | None = Field(default=None, max_length=255)
    profit_margin: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=1000)
    org_unit_id: str | None = Field(default=None, max_length=64)
    join_date: str | None = Field(default=None, pattern=r"^$|^\d{4}-\d{2}-\d{2}$")


class EnrollmentPaymentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_status: Literal["PAID", "WAIVED", "SPECIAL_APPROVED"] = "PAID"
    amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    note: str | None = Field(default=None, max_length=1000)


class EnrollmentRejectPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=4, max_length=2000)


class EnrollmentLinkPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="学长服务助手-新学长信息登记", min_length=1, max_length=255)


class MiniProgramCodePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_token: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9!#$&'()*+,/:;=?@._~-]+$",
    )


def _service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    return HTTPException(400, str(exc))


def _client_address(request: Request) -> str:
    """Resolve the nearest externally forwarded address without logging it."""
    forwarded = request.headers.get("x-forwarded-for", "")
    candidates = [item.strip() for item in forwarded.split(",") if item.strip()]
    if request.headers.get("x-real-ip"):
        candidates.append(request.headers["x-real-ip"].strip())
    for candidate in reversed(candidates):
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return request.client.host if request.client else "unknown"


@router.get("/public/enrollment/{token}")
def public_enrollment_form(token: str) -> dict:
    try:
        data = get_public_enrollment_form(token)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/public/enrollment/{token}")
def submit_enrollment(
    token: str, payload: PublicEnrollmentPayload, request: Request
) -> dict:
    try:
        data = submit_public_enrollment(
            token,
            payload.model_dump(exclude={"privacy_consent"}),
            _client_address(request),
        )
    except EnrollmentRateLimitError as exc:
        raise HTTPException(429, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/enrollment-applications")
def enrollment_applications(
    application_status: Literal[
        "SUBMITTED", "APPROVED", "REJECTED", "ENROLLED", "CANCELLED"
    ]
    | None = None,
    payment_status: Literal[
        "UNCONFIRMED", "PAID", "WAIVED", "SPECIAL_APPROVED"
    ]
    | None = None,
    query: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=100, ge=1, le=200),
    user: dict = Depends(require_permission("enrollment:read")),
) -> dict:
    return {
        "success": True,
        "data": list_enrollment_applications(
            user["id"],
            application_status=application_status,
            payment_status=payment_status,
            query=query,
            limit=limit,
        ),
    }


@router.get("/enrollment-applications/{application_id}")
def enrollment_application_detail(
    application_id: int,
    user: dict = Depends(require_permission("enrollment:read")),
) -> dict:
    try:
        data = get_enrollment_application(user["id"], application_id)
    except (PermissionError, ValueError) as exc:
        raise _service_error(exc) from exc
    return {"success": True, "data": data}


@router.patch("/enrollment-applications/{application_id}/review")
def review_application(
    application_id: int,
    payload: EnrollmentReviewPayload,
    user: dict = Depends(require_permission("enrollment:review")),
) -> dict:
    values = payload.model_dump(exclude_unset=True)
    decision = values.pop("decision", payload.decision)
    review_note = values.pop("review_note", None)
    try:
        data = review_enrollment_application(
            user["id"],
            application_id,
            decision=decision,
            updates=values,
            review_note=review_note,
        )
    except (PermissionError, ValueError) as exc:
        raise _service_error(exc) from exc
    return {"success": True, "data": data}


@router.post("/enrollment-applications/{application_id}/payment-confirmation")
def confirm_payment(
    application_id: int,
    payload: EnrollmentPaymentPayload,
    user: dict = Depends(require_permission("enrollment:payment_confirm")),
) -> dict:
    try:
        data = confirm_enrollment_payment(
            user["id"],
            application_id,
            payment_status=payload.payment_status,
            amount=payload.amount,
            note=payload.note,
        )
    except (PermissionError, ValueError) as exc:
        raise _service_error(exc) from exc
    return {"success": True, "data": data}


@router.post("/enrollment-applications/{application_id}/reject")
def reject_application(
    application_id: int,
    payload: EnrollmentRejectPayload,
    user: dict = Depends(require_permission("enrollment:review")),
) -> dict:
    try:
        data = reject_enrollment_application(
            user["id"], application_id, payload.reason
        )
    except (PermissionError, ValueError) as exc:
        raise _service_error(exc) from exc
    return {"success": True, "data": data}


@router.post("/enrollment-applications/{application_id}/enroll")
def enroll_member(
    application_id: int,
    user: dict = Depends(require_permission("enrollment:enroll")),
) -> dict:
    try:
        data = enroll_application(user["id"], application_id)
    except (PermissionError, ValueError) as exc:
        raise _service_error(exc) from exc
    return {"success": True, "data": data}


@router.get("/enrollment-links/active")
def active_link(
    _: dict = Depends(require_permission("enrollment:manage_link")),
) -> dict:
    return {"success": True, "data": get_active_enrollment_link()}


@router.post("/enrollment-links")
def create_link(
    payload: EnrollmentLinkPayload,
    user: dict = Depends(require_permission("enrollment:manage_link")),
) -> dict:
    try:
        data = create_enrollment_link(user["id"], payload.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/enrollment-links/{link_id}/rotate")
def rotate_link(
    link_id: int,
    user: dict = Depends(require_permission("enrollment:manage_link")),
) -> dict:
    try:
        data = rotate_enrollment_link(user["id"], link_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/enrollment-links/{link_id}/disable")
def disable_link(
    link_id: int,
    user: dict = Depends(require_permission("enrollment:manage_link")),
) -> dict:
    try:
        data = disable_enrollment_link(user["id"], link_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/enrollment-links/{link_id}/mini-program-code")
def mini_program_code(
    link_id: int,
    payload: MiniProgramCodePayload,
    user: dict = Depends(require_permission("enrollment:manage_link")),
) -> dict:
    try:
        data = generate_wechat_miniprogram_code(
            user["id"], link_id, payload.raw_token
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}
