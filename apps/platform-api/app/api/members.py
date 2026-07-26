from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.services.members import (
    create_member,
    create_sensitive_export,
    download_sensitive_export,
    list_members,
    normal_export_csv,
    reveal_contact,
)


router = APIRouter(prefix="/api/v1", tags=["members-privacy"])


class MemberCreatePayload(BaseModel):
    member_code: str | None = Field(default=None, min_length=2, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    org_unit_id: str
    development_org_unit_id: str | None = None
    phone: str
    company_name: str | None = None
    gender: str | None = Field(default=None, max_length=32)
    district: str | None = Field(default=None, max_length=255)
    company_address: str | None = Field(default=None, max_length=1000)
    class_name: str | None = Field(default=None, max_length=255)
    group_name: str | None = Field(default=None, max_length=255)
    birthday: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    join_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    study_start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    membership_years: float | None = Field(default=None, ge=0, le=100)
    renewal_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|INACTIVE|SUSPENDED)$")
    position: str | None = Field(default=None, max_length=255)
    referrer: str | None = Field(default=None, max_length=255)
    referrer_center: str | None = Field(default=None, max_length=255)
    industry_category: str | None = Field(default=None, max_length=255)
    industry: str | None = Field(default=None, max_length=255)
    company_products: str | None = Field(default=None, max_length=4000)
    annual_sales: str | None = Field(default=None, max_length=255)
    company_size: str | None = Field(default=None, max_length=255)
    profit_margin: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=4000)


class ContactAccessPayload(BaseModel):
    task_id: int
    purpose: str = Field(min_length=4, max_length=500)
    client_reference: str | None = Field(default=None, max_length=255)


class SensitiveExportPayload(BaseModel):
    purpose: str = Field(min_length=6, max_length=1000)
    second_confirmed: bool


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
