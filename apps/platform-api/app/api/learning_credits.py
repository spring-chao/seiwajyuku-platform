from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import require_permission
from app.services.learning_credits import (
    LearningCreditError,
    dry_run_study_meeting_settlement,
    dry_run_study_meetings,
    list_credit_entries,
    member_credit_summary,
    reverse_credit_entry,
    settle_study_meeting,
)
from app.services.class_meeting_credits import (
    dry_run_class_meeting_settlement,
    dry_run_class_meetings,
)
from app.services.learning_activity_credits import (
    dry_run_daily_reading,
    dry_run_excellent_shares,
    get_business_calendar,
    record_learning_activity_fact,
    save_business_calendar,
)
from app.services.hq_reading_import import (
    confirm_hq_reading_identities,
    dry_run_hq_reading_import,
    import_hq_reading_workbook,
    record_manual_verified_excellent_share,
)
from app.services.historical_credit_import import (
    dry_run_historical_credit_import,
    get_historical_credit_import_batch,
    get_historical_credit_import_row,
    list_historical_credit_import_anomalies,
    register_suzhou_credit_workbook,
)
from app.services.historical_credit_review import (
    accept_year_only_period,
    approve_calculated_totals,
    bulk_confirm_high_confidence_matches,
    confirm_class_mapping,
    confirm_member_match,
    confirm_month_period,
    confirm_no_credit,
    get_historical_credit_review_workbench,
    resolve_credit_anomalies,
    upsert_class_mapping_candidates,
)


router = APIRouter(prefix="/api/v1/learning-credits", tags=["learning-credits"])


class ReversalPayload(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class LearningActivityFactPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_type: str = Field(pattern="^(DAILY_READING|EXCELLENT_SHARE)$")
    member_id: int = Field(gt=0)
    class_org_unit_id: str = Field(min_length=1, max_length=64)
    occurred_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    source_type: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=255)
    participation_status: str = Field(
        default="RECORDED", pattern="^(RECORDED|CONFIRMED|REJECTED|CANCELLED)$"
    )
    title: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)
    binding_id: int | None = Field(default=None, gt=0)


class HqIdentityConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_identity_key: str = Field(min_length=1, max_length=255)
    member_id: int = Field(gt=0)


class HqIdentityConfirmationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmations: list[HqIdentityConfirmation] = Field(min_length=1, max_length=500)


class ManualExcellentSharePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: int = Field(gt=0)
    class_org_unit_id: str = Field(min_length=1, max_length=64)
    occurred_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    note: str | None = Field(default=None, max_length=1000)
    evidence: str | None = Field(default=None, max_length=1000)


class BusinessCalendarDayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    day_type: str = Field(
        pattern="^(NORMAL_WORKDAY|WEEKEND|HOLIDAY|ADJUSTED_WORKDAY)$"
    )
    note: str | None = Field(default=None, max_length=500)


class BusinessCalendarPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendar_year: int = Field(ge=2000, le=2100)
    version_label: str = Field(min_length=1, max_length=64)
    status: str = Field(default="DRAFT", pattern="^(DRAFT|PUBLISHED)$")
    days: list[BusinessCalendarDayPayload] = Field(default_factory=list, max_length=366)


class HistoricalClassMappingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_sheet: str = Field(min_length=1, max_length=255)
    raw_class_name: str | None = Field(default=None, max_length=255)
    org_unit_id: str | None = Field(default=None, max_length=64)
    mapping_status: str = Field(default="PENDING_REVIEW", max_length=32)
    mapping_reason: str = Field(min_length=1, max_length=255)
    candidate_org_unit_ids: list[str] = Field(default_factory=list, max_length=50)
    evidence: dict[str, Any] = Field(default_factory=dict)


class HistoricalClassMappingCandidatesPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mappings: list[HistoricalClassMappingCandidate] = Field(min_length=1, max_length=500)
    snapshot_id: str | None = Field(default=None, max_length=128)
    snapshot_fingerprint: str | None = Field(default=None, max_length=128)


class HistoricalClassMappingConfirmationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping_id: int = Field(gt=0)
    confirmed_org_unit_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=1000)
    snapshot_id: str = Field(min_length=1, max_length=128)
    snapshot_fingerprint: str = Field(min_length=1, max_length=128)


class HistoricalBulkMatchConfirmationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_count: int = Field(ge=0, le=100000)
    snapshot_id: str = Field(min_length=1, max_length=128)
    snapshot_fingerprint: str = Field(min_length=1, max_length=128)
    algorithm_version: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=1000)


class HistoricalRowsDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_ids: list[int] = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=1000)


class HistoricalMemberMatchConfirmationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: int = Field(gt=0)
    member_id: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=1000)
    snapshot_id: str = Field(min_length=1, max_length=128)
    snapshot_fingerprint: str = Field(min_length=1, max_length=128)


class HistoricalCreditResolutionPayload(HistoricalRowsDecisionPayload):
    resolution: str = Field(pattern="^(REJECTED|NEEDS_SOURCE_CORRECTION)$")


class HistoricalYearAcceptancePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_count: int = Field(ge=0, le=100000)
    reason: str = Field(min_length=1, max_length=1000)
    legacy_credit_type: str | None = Field(default=None, max_length=64)
    source_sheet: str | None = Field(default=None, max_length=255)
    source_column_name: str | None = Field(default=None, max_length=255)


class HistoricalMonthConfirmationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_ids: list[int] = Field(min_length=1, max_length=5000)
    reason: str = Field(min_length=1, max_length=1000)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    return HTTPException(400, str(exc))


def _read_hq_workbook_name(workbook: UploadFile) -> str:
    if not (workbook.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "总部每日读书导入只接受 .xlsx 工作簿")
    return workbook.filename or "hq-reading-export.xlsx"


def _read_historical_workbook_name(workbook: UploadFile) -> str:
    if not (workbook.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "历史学分导入只接受 .xlsx 工作簿")
    return workbook.filename or "historical-credit-import.xlsx"


@router.post("/historical-imports")
async def register_historical_credit_import(
    workbook: UploadFile = File(...),
    source_year: int = Form(default=2026, ge=2000, le=2100),
    source_name: str = Form(default="苏州分中心2026年学分", min_length=1, max_length=255),
    user: dict = Depends(require_permission("plans:historical_credit_import_manage")),
) -> dict:
    filename = _read_historical_workbook_name(workbook)
    content = await workbook.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "历史学分工作簿超过20MB限制")
    try:
        data = register_suzhou_credit_workbook(
            content=content,
            original_filename=filename,
            actor_user_id=user["id"],
            source_year=source_year,
            source_name=source_name,
        )
        return {"success": True, "data": data}
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.get("/historical-imports/{batch_id}")
def historical_credit_import_batch(
    batch_id: int,
    user: dict = Depends(require_permission("plans:historical_credit_import_manage")),
) -> dict:
    try:
        return {"success": True, "data": get_historical_credit_import_batch(batch_id)}
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.get("/historical-imports/{batch_id}/anomalies")
def historical_credit_import_anomalies(
    batch_id: int,
    user: dict = Depends(require_permission("plans:historical_credit_import_manage")),
) -> dict:
    try:
        return {"success": True, "data": list_historical_credit_import_anomalies(batch_id)}
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.get("/historical-imports/{batch_id}/rows/{row_id}")
def historical_credit_import_row(
    batch_id: int,
    row_id: int,
    user: dict = Depends(require_permission("plans:historical_credit_import_manage")),
) -> dict:
    try:
        return {"success": True, "data": get_historical_credit_import_row(batch_id, row_id)}
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/historical-imports/{batch_id}/dry-run")
def dry_run_historical_credit_import_endpoint(
    batch_id: int,
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    try:
        return {"success": True, "data": dry_run_historical_credit_import(batch_id)}
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.get("/historical-imports/{batch_id}/review-workbench")
def historical_credit_review_workbench(
    batch_id: int,
    user: dict = Depends(require_permission("plans:historical_credit_import_manage")),
) -> dict:
    try:
        return {"success": True, "data": get_historical_credit_review_workbench(batch_id)}
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/historical-imports/{batch_id}/class-mappings/candidates")
def historical_class_mapping_candidates(
    batch_id: int,
    payload: HistoricalClassMappingCandidatesPayload,
    user: dict = Depends(require_permission("plans:historical_credit_import_manage")),
) -> dict:
    try:
        return {
            "success": True,
            "data": upsert_class_mapping_candidates(
                batch_id=batch_id,
                mappings=[item.model_dump() for item in payload.mappings],
                snapshot_id=payload.snapshot_id,
                snapshot_fingerprint=payload.snapshot_fingerprint,
                actor_user_id=user["id"],
            ),
        }
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/historical-imports/{batch_id}/class-mappings/confirm")
def historical_class_mapping_confirm(
    batch_id: int,
    payload: HistoricalClassMappingConfirmationPayload,
    user: dict = Depends(require_permission("plans:historical_credit_import_manage")),
) -> dict:
    try:
        return {
            "success": True,
            "data": confirm_class_mapping(
                batch_id=batch_id,
                mapping_id=payload.mapping_id,
                confirmed_org_unit_id=payload.confirmed_org_unit_id,
                actor_user_id=user["id"],
                reason=payload.reason,
                snapshot_id=payload.snapshot_id,
                snapshot_fingerprint=payload.snapshot_fingerprint,
            ),
        }
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/historical-imports/{batch_id}/matches/bulk-confirm")
def historical_bulk_match_confirm(
    batch_id: int,
    payload: HistoricalBulkMatchConfirmationPayload,
    user: dict = Depends(require_permission("plans:historical_credit_import_manage")),
) -> dict:
    try:
        return {
            "success": True,
            "data": bulk_confirm_high_confidence_matches(
                batch_id=batch_id,
                expected_count=payload.expected_count,
                snapshot_id=payload.snapshot_id,
                snapshot_fingerprint=payload.snapshot_fingerprint,
                algorithm_version=payload.algorithm_version,
                actor_user_id=user["id"],
                reason=payload.reason,
            ),
        }
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/historical-imports/{batch_id}/matches/confirm")
def historical_member_match_confirm(
    batch_id: int,
    payload: HistoricalMemberMatchConfirmationPayload,
    user: dict = Depends(require_permission("plans:historical_credit_import_manage")),
) -> dict:
    try:
        return {
            "success": True,
            "data": confirm_member_match(
                batch_id=batch_id, row_id=payload.row_id, member_id=payload.member_id,
                actor_user_id=user["id"], reason=payload.reason,
                snapshot_id=payload.snapshot_id, snapshot_fingerprint=payload.snapshot_fingerprint,
            ),
        }
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/historical-imports/{batch_id}/credit/approve-totals")
def historical_credit_totals_approve(
    batch_id: int,
    payload: HistoricalRowsDecisionPayload,
    user: dict = Depends(require_permission("plans:historical_credit_import_manage")),
) -> dict:
    try:
        return {
            "success": True,
            "data": approve_calculated_totals(
                batch_id=batch_id, row_ids=payload.row_ids,
                actor_user_id=user["id"], reason=payload.reason,
            ),
        }
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/historical-imports/{batch_id}/credit/confirm-zero")
def historical_credit_zero_confirm(
    batch_id: int,
    payload: HistoricalRowsDecisionPayload,
    user: dict = Depends(require_permission("plans:historical_credit_import_manage")),
) -> dict:
    try:
        return {
            "success": True,
            "data": confirm_no_credit(
                batch_id=batch_id, row_ids=payload.row_ids,
                actor_user_id=user["id"], reason=payload.reason,
            ),
        }
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/historical-imports/{batch_id}/credit/resolve-anomalies")
def historical_credit_anomaly_resolve(
    batch_id: int,
    payload: HistoricalCreditResolutionPayload,
    user: dict = Depends(require_permission("plans:historical_credit_import_manage")),
) -> dict:
    try:
        return {
            "success": True,
            "data": resolve_credit_anomalies(
                batch_id=batch_id, row_ids=payload.row_ids, resolution=payload.resolution,
                actor_user_id=user["id"], reason=payload.reason,
            ),
        }
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/historical-imports/{batch_id}/period/accept-year")
def historical_period_year_accept(
    batch_id: int,
    payload: HistoricalYearAcceptancePayload,
    user: dict = Depends(require_permission("plans:historical_credit_import_manage")),
) -> dict:
    try:
        return {
            "success": True,
            "data": accept_year_only_period(
                batch_id=batch_id, expected_count=payload.expected_count,
                actor_user_id=user["id"], reason=payload.reason,
                legacy_credit_type=payload.legacy_credit_type,
                source_sheet=payload.source_sheet,
                source_column_name=payload.source_column_name,
            ),
        }
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/historical-imports/{batch_id}/period/confirm-month")
def historical_period_month_confirm(
    batch_id: int,
    payload: HistoricalMonthConfirmationPayload,
    user: dict = Depends(require_permission("plans:historical_credit_import_manage")),
) -> dict:
    try:
        return {
            "success": True,
            "data": confirm_month_period(
                batch_id=batch_id, item_ids=payload.item_ids,
                actor_user_id=user["id"], reason=payload.reason,
            ),
        }
    except (ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/hq-reading/import")
async def import_hq_reading(
    target_class_org_unit_id: str = Form(..., min_length=1, max_length=64),
    workbook: UploadFile = File(...),
    user: dict = Depends(require_permission("plans:hq_reading_import_manage")),
) -> dict:
    filename = _read_hq_workbook_name(workbook)
    content = await workbook.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "总部每日读书工作簿超过20MB限制")
    try:
        data = import_hq_reading_workbook(
            actor_user_id=user["id"],
            target_class_org_unit_id=target_class_org_unit_id,
            original_filename=filename,
            content=content,
        )
        return {"success": True, "data": data}
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/hq-reading/{batch_id}/confirm-identities")
def confirm_hq_reading(
    batch_id: int,
    payload: HqIdentityConfirmationPayload,
    user: dict = Depends(require_permission("plans:hq_reading_import_manage")),
) -> dict:
    try:
        data = confirm_hq_reading_identities(
            actor_user_id=user["id"],
            batch_id=batch_id,
            confirmations=[item.model_dump() for item in payload.confirmations],
        )
        return {"success": True, "data": data}
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/hq-reading/{batch_id}/dry-run")
def dry_run_hq_reading(
    batch_id: int,
    limit: int = Query(default=500, ge=1, le=5000),
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    try:
        data = dry_run_hq_reading_import(
            actor_user_id=user["id"], batch_id=batch_id, limit=limit
        )
        return {"success": True, "data": data}
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/excellent-shares/manual-verified")
def create_manual_verified_excellent_share(
    payload: ManualExcellentSharePayload,
    user: dict = Depends(require_permission("plans:hq_reading_import_manage")),
) -> dict:
    try:
        data = record_manual_verified_excellent_share(
            actor_user_id=user["id"], **payload.model_dump()
        )
        return {"success": True, "data": data}
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/dry-run/daily-reading")
def dry_run_daily_reading_activity(
    member_id: int | None = Query(default=None, ge=1),
    class_org_unit_id: str | None = Query(default=None, max_length=64),
    occurred_from: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    occurred_to: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(default=500, ge=1, le=500),
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    try:
        return {
            "success": True,
            "data": dry_run_daily_reading(
                actor_user_id=user["id"],
                member_id=member_id,
                class_org_unit_id=class_org_unit_id,
                occurred_from=occurred_from,
                occurred_to=occurred_to,
                limit=limit,
            ),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/dry-run/excellent-shares")
def dry_run_excellent_share_activity(
    member_id: int | None = Query(default=None, ge=1),
    class_org_unit_id: str | None = Query(default=None, max_length=64),
    occurred_from: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    occurred_to: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(default=500, ge=1, le=500),
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    try:
        return {
            "success": True,
            "data": dry_run_excellent_shares(
                actor_user_id=user["id"],
                member_id=member_id,
                class_org_unit_id=class_org_unit_id,
                occurred_from=occurred_from,
                occurred_to=occurred_to,
                limit=limit,
            ),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/activity-facts")
def create_learning_activity_fact(
    payload: LearningActivityFactPayload,
    user: dict = Depends(require_permission("plans:credit_activity_fact_manage")),
) -> dict:
    try:
        data = record_learning_activity_fact(
            actor_user_id=user["id"], **payload.model_dump()
        )
        return {"success": True, "data": data}
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.get("/business-calendars/{calendar_year}")
def business_calendar(
    calendar_year: int,
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    try:
        return {"success": True, "data": get_business_calendar(calendar_year=calendar_year)}
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/business-calendars")
def save_business_calendar_endpoint(
    payload: BusinessCalendarPayload,
    user: dict = Depends(require_permission("plans:business_calendar_manage")),
) -> dict:
    try:
        data = save_business_calendar(
            actor_user_id=user["id"],
            calendar_year=payload.calendar_year,
            version_label=payload.version_label,
            status=payload.status,
            days=[day.model_dump() for day in payload.days],
        )
        return {"success": True, "data": data}
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/dry-run/class-meetings/{event_group_id}")
def dry_run_class_meeting(
    event_group_id: int,
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    """Project one attendance event group into the learning-credit model."""
    try:
        return {
            "success": True,
            "data": dry_run_class_meeting_settlement(
                actor_user_id=user["id"], event_group_id=event_group_id
            ),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/dry-run/class-meetings")
def dry_run_class_meeting_batch(
    class_org_unit_id: str | None = Query(default=None, max_length=64),
    event_date_from: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    event_date_to: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(default=30, ge=1, le=500),
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    """Project class meetings in a class/date window without writing credits."""
    try:
        return {
            "success": True,
            "data": dry_run_class_meetings(
                actor_user_id=user["id"],
                class_org_unit_id=class_org_unit_id,
                event_date_from=event_date_from,
                event_date_to=event_date_to,
                limit=limit,
            ),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/dry-run/study-meetings/{session_id}")
def dry_run_study_meeting(
    session_id: int,
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    try:
        return {
            "success": True,
            "data": dry_run_study_meeting_settlement(
                actor_user_id=user["id"], session_id=session_id
            ),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/dry-run/study-meetings")
def dry_run_study_meeting_batch(
    class_org_unit_id: str | None = Query(default=None, max_length=64),
    learning_cycle_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=30, ge=1, le=500),
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    try:
        return {
            "success": True,
            "data": dry_run_study_meetings(
                actor_user_id=user["id"], class_org_unit_id=class_org_unit_id,
                learning_cycle_id=learning_cycle_id, limit=limit,
            ),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.get("/entries")
def credit_entries(
    member_id: int | None = Query(default=None, ge=1),
    occurred_from: str | None = Query(default=None, max_length=32),
    occurred_to: str | None = Query(default=None, max_length=32),
    credit_category: str | None = Query(default=None, max_length=32),
    source_type: str | None = Query(default=None, max_length=64),
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    try:
        data = list_credit_entries(
            actor_user_id=user["id"], member_id=member_id,
            occurred_from=occurred_from, occurred_to=occurred_to,
            credit_category=credit_category, source_type=source_type,
        )
        return {"success": True, "data": {"entries": data}}
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.get("/members/{member_id}/summary")
def credit_summary(
    member_id: int,
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    try:
        return {
            "success": True,
            "data": member_credit_summary(actor_user_id=user["id"], member_id=member_id),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/study-meetings/{session_id}/settle")
def settle_meeting(
    session_id: int,
    user: dict = Depends(require_permission("plans:credit_settlement_manage")),
) -> dict:
    try:
        return {
            "success": True,
            "data": settle_study_meeting(
                actor_user_id=user["id"], session_id=session_id
            ),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/entries/{entry_id}/reverse")
def reverse_entry(
    entry_id: int,
    payload: ReversalPayload,
    user: dict = Depends(require_permission("plans:credit_settlement_manage")),
) -> dict:
    try:
        return {
            "success": True,
            "data": reverse_credit_entry(
                actor_user_id=user["id"], entry_id=entry_id, reason=payload.reason
            ),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc
