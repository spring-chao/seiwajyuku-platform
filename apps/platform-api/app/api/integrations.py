from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.services.integrations import (
    activity_admin_view,
    calculate_monthly_metrics,
    ingest_snapshots,
)


router = APIRouter(prefix="/api/v1", tags=["integrations"])


class SnapshotEvent(BaseModel):
    external_id: str
    org_unit_id: str
    activity_type: str
    occurred_at: str
    eligible_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    participant_phone: str | None = None
    participant_ref: str | None = None
    title: str | None = None
    status: str = "COMPLETED"


class SnapshotPayload(BaseModel):
    source_key: str = Field(min_length=2, max_length=128)
    events: list[SnapshotEvent]


class CalculatePayload(BaseModel):
    annual_plan_id: int
    source_key: str
    year: int = Field(ge=2020, le=2100)
    month: int = Field(ge=1, le=12)


@router.post("/integrations/{snapshot_type}/snapshots")
def receive_snapshots(
    snapshot_type: str,
    payload: SnapshotPayload,
    x_api_key: str = Header(alias="X-API-Key"),
) -> dict:
    try:
        data = ingest_snapshots(
            source_key=payload.source_key,
            snapshot_type=snapshot_type,
            api_key=x_api_key,
            events=[event.model_dump() for event in payload.events],
        )
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/integrations/calculate")
def calculate(
    payload: CalculatePayload,
    user: dict = Depends(require_permission("integrations:manage")),
) -> dict:
    try:
        rows = calculate_monthly_metrics(**payload.model_dump())
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": rows}


@router.get("/activities")
def activities(
    month: str | None = None,
    user: dict = Depends(require_permission("org:read")),
) -> dict:
    return {"success": True, "data": activity_admin_view(user["id"], month)}
