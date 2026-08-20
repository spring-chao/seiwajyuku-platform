from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.services.members import (
    create_member,
    create_sensitive_export,
    can_access_member,
    download_sensitive_export,
    get_member_change_history,
    get_member_detail,
    get_member_edit_profile,
    get_member_enterprise_detail,
    get_member_timeline,
    list_members,
    normal_export_csv,
    record_member_service_signal_feedback,
    reveal_contact,
)
from app.services.birthday_greetings import (
    generate_birthday_greeting_draft,
    get_birthday_greeting_context,
)
from app.services.iam import accessible_org_ids
from app.db import fetch_all, fetch_one


router = APIRouter(prefix="/api/v1", tags=["members-privacy"])


class MemberCreatePayload(BaseModel):
    member_code: str | None = Field(default=None, min_length=2, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    org_unit_id: str
    development_org_unit_id: str | None = None
    phone: str | None = None
    company_name: str | None = None
    gender: str | None = Field(default=None, max_length=32)
    district: str | None = Field(default=None, max_length=255)
    company_address: str | None = Field(default=None, max_length=1000)
    class_name: str | None = Field(default=None, max_length=255)
    class_committee_name: str | None = Field(default=None, max_length=255)
    group_name: str | None = Field(default=None, max_length=255)
    class_org_unit_id: str | None = None
    group_org_unit_id: str | None = None
    birthday: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    join_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    study_start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    membership_years: float | None = Field(default=None, ge=0, le=100)
    renewal_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    renewal_month_overridden: bool | None = None
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|INACTIVE|SUSPENDED)$")
    position: str | None = Field(default=None, max_length=255)
    referrer: str | None = Field(default=None, max_length=255)
    referrer_center: str | None = Field(default=None, max_length=255)
    industry_category: str | None = Field(default=None, max_length=255)
    industry: str | None = Field(default=None, max_length=255)
    company_products: str | None = Field(default=None, max_length=4000)
    annual_sales: str | None = Field(default=None, max_length=255)
    employee_count: int | None = Field(default=None, ge=0, le=10000000)
    company_size: str | None = Field(default=None, max_length=255)
    profit_margin: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=4000)


class ContactAccessPayload(BaseModel):
    task_id: int
    purpose: str = Field(min_length=4, max_length=500)
    client_reference: str | None = Field(default=None, max_length=255)


class MemberUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE|SUSPENDED)$")
    phone: str | None = Field(default=None, pattern=r"^$|^1\d{10}$")
    company_name: str | None = Field(default=None, max_length=500)
    gender: str | None = Field(default=None, max_length=32)
    district: str | None = Field(default=None, max_length=255)
    company_address: str | None = Field(default=None, max_length=1000)
    class_committee_name: str | None = Field(default=None, max_length=255)
    birthday: str | None = Field(default=None, pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    join_date: str | None = Field(default=None, pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    study_start_date: str | None = Field(default=None, pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    membership_years: float | None = Field(default=None, ge=0, le=100)
    renewal_month: str | None = Field(default=None, pattern=r"^$|^\d{4}-\d{2}$")
    renewal_month_overridden: bool | None = None
    position: str | None = Field(default=None, max_length=255)
    referrer: str | None = Field(default=None, max_length=255)
    referrer_center: str | None = Field(default=None, max_length=255)
    industry_category: str | None = Field(default=None, max_length=255)
    industry: str | None = Field(default=None, max_length=255)
    company_products: str | None = Field(default=None, max_length=4000)
    annual_sales: str | None = Field(default=None, max_length=255)
    employee_count: int | None = Field(default=None, ge=0, le=10000000)
    company_size: str | None = Field(default=None, max_length=255)
    profit_margin: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=4000)
    org_unit_id: str | None = None
    development_org_unit_id: str | None = None
    class_org_unit_id: str | None = None
    group_org_unit_id: str | None = None


class MemberMergePayload(BaseModel):
    survivor_member_id: int
    reason: str = Field(min_length=6, max_length=1000)


class SensitiveExportPayload(BaseModel):
    purpose: str = Field(min_length=6, max_length=1000)
    second_confirmed: bool


class MemberServiceSignalFeedbackPayload(BaseModel):
    rule_version: str = Field(min_length=1, max_length=64)
    status: str = Field(
        pattern="^(CONFIRMED_VALID|NOT_APPLICABLE|DATA_CORRECTED)$"
    )


class BirthdayGreetingDraftPayload(BaseModel):
    selected_memory_ids: list[str] = Field(default_factory=list, max_length=4)
    tone: Literal["standard", "warm", "concise"] = "warm"


@router.post("/members")
def add_member(
    payload: MemberCreatePayload,
    user: dict = Depends(require_permission("members:manage")),
) -> dict:
    try:
        member_id = create_member(user["id"], **payload.model_dump())
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": {"id": member_id}}


@router.patch("/members/{member_id}")
def edit_member(
    member_id: int,
    payload: MemberUpdatePayload,
    user: dict = Depends(require_permission("members:manage")),
) -> dict:
    from app.services.members import update_member

    try:
        update_member(
            user["id"], member_id, payload.model_dump(exclude_unset=True)
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": {"id": member_id}}


@router.post("/members/{member_id}/merge")
def merge_member(
    member_id: int,
    payload: MemberMergePayload,
    user: dict = Depends(require_permission("members:manage")),
) -> dict:
    from app.services.members import merge_members

    try:
        merge_members(
            user["id"], payload.survivor_member_id, member_id, payload.reason
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "success": True,
        "data": {
            "survivor_member_id": payload.survivor_member_id,
            "merged_member_id": member_id,
        },
    }


@router.get("/members")
def members(
    org_unit_id: str | None = None,
    user: dict = Depends(require_permission("members:read")),
) -> dict:
    return {"success": True, "data": list_members(user["id"], org_unit_id)}


@router.post("/members/{member_id}/contact-access")
def contact_access(
    member_id: int,
    payload: ContactAccessPayload,
    user: dict = Depends(require_permission("contact:reveal")),
) -> dict:
    try:
        data = reveal_contact(
            member_id=member_id,
            task_id=payload.task_id,
            actor_user_id=user["id"],
            purpose=payload.purpose,
            client_reference=payload.client_reference,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


class EnterpriseDetailPayload(BaseModel):
    purpose: str = Field(min_length=4, max_length=500)


@router.get("/members/{member_id}/detail")
def member_detail(
    member_id: int,
    user: dict = Depends(require_permission("members:detail_view")),
) -> dict:
    """返回学长基本资料（手机号脱敏，不含企业敏感财务数据）。"""
    try:
        data = get_member_detail(member_id, user["id"])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/members/{member_id}/edit-profile")
def member_edit_profile(
    member_id: int,
    user: dict = Depends(require_permission("members:manage")),
) -> dict:
    """Return the current editable profile for a scoped, audited operation."""
    try:
        data = get_member_edit_profile(member_id, user["id"])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/members/{member_id}/change-history")
def member_change_history(
    member_id: int,
    user: dict = Depends(require_permission("members:detail_view")),
) -> dict:
    try:
        data = get_member_change_history(member_id, user["id"])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/members/{member_id}/timeline")
def member_timeline(
    member_id: int,
    limit: int = Query(default=100, ge=1, le=200),
    user: dict = Depends(require_permission("members:detail_view")),
) -> dict:
    try:
        data = get_member_timeline(member_id, user["id"], limit=limit)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/members/{member_id}/birthday-greeting-context")
def birthday_greeting_context(
    member_id: int,
    user: dict = Depends(require_permission("members:detail_view")),
) -> dict:
    try:
        data = get_birthday_greeting_context(member_id, user["id"])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/members/{member_id}/birthday-greeting-draft")
def birthday_greeting_draft(
    member_id: int,
    payload: BirthdayGreetingDraftPayload,
    user: dict = Depends(require_permission("members:detail_view")),
) -> dict:
    try:
        data = generate_birthday_greeting_draft(
            member_id,
            user["id"],
            selected_memory_ids=payload.selected_memory_ids,
            tone=payload.tone,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/members/{member_id}/service-signals/{signal_code}/feedback")
def member_service_signal_feedback(
    member_id: int,
    signal_code: str,
    payload: MemberServiceSignalFeedbackPayload,
    user: dict = Depends(require_permission("members:manage")),
) -> dict:
    try:
        data = record_member_service_signal_feedback(
            member_id,
            user["id"],
            signal_code=signal_code,
            rule_version=payload.rule_version,
            feedback_status=payload.status,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/members/{member_id}/enterprise-detail")
def enterprise_detail(
    member_id: int,
    payload: EnterpriseDetailPayload,
    user: dict = Depends(require_permission("members:enterprise_view")),
) -> dict:
    """返回企业敏感资料；完整手机号仍须通过有效联系任务临时查看。"""
    try:
        data = get_member_enterprise_detail(member_id, user["id"], payload.purpose)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/exports/members")
def export_members(
    user: dict = Depends(require_permission("exports:normal")),
) -> PlainTextResponse:
    return PlainTextResponse(
        normal_export_csv(user["id"]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=members-masked.csv"},
    )


@router.post("/exports")
def create_export(
    payload: SensitiveExportPayload,
    user: dict = Depends(require_permission("exports:sensitive")),
) -> dict:
    try:
        job_id = create_sensitive_export(
            user["id"], payload.purpose, payload.second_confirmed
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": {"id": job_id, "expires_in": 900}}


@router.get("/exports/{job_id}")
def download_export(
    job_id: int,
    user: dict = Depends(require_permission("exports:sensitive")),
) -> PlainTextResponse:
    try:
        content = download_sensitive_export(job_id, user["id"])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return PlainTextResponse(
        content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=sensitive-members-{job_id}.csv"},
    )


@router.get("/members/{member_id}/attendance-scores")
def member_attendance_scores(
    member_id: int,
    user: dict = Depends(require_permission("members:read")),
) -> dict:
    """Get attendance score detail for a member."""
    member = fetch_one("SELECT org_unit_id FROM members WHERE id=?", (member_id,))
    if not member:
        raise HTTPException(404, "学长不存在")
    allowed = accessible_org_ids(user["id"])
    if not can_access_member(member_id, member["org_unit_id"], allowed):
        raise HTTPException(403, "学长不在组织授权范围内")
    from app.services.attendance_scoring import member_scores
    scores = member_scores(member_id)
    total = sum(s["final_points"] for s in scores)
    return {
        "success": True,
        "data": {
            "member_id": member_id,
            "total_points": total,
            "records": scores,
        },
    }
