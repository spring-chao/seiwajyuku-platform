"""Bounded scheduler entry point; it exposes only aggregate monthly counts."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from app.api.study_evidence_cleanup import require_maintenance_token
from app.services.class_meeting_next_day import refresh_due_class_meetings

router = APIRouter(prefix="/api/v1/internal/learning-cycle-monthly", tags=["internal-maintenance"])


class RefreshPayload(BaseModel):
    # A caller cannot choose dates, classes, repair snapshots or credit modes.
    model_config = ConfigDict(extra="forbid")


@router.post("")
def refresh_monthly_content(
    payload: RefreshPayload,
    x_study_evidence_cleanup_token: str | None = Header(
        default=None, alias="X-Study-Evidence-Cleanup-Token"
    ),
) -> dict:
    require_maintenance_token(x_study_evidence_cleanup_token)
    try:
        from app.services.renewal_expiry import sync_expired_members
        renewal_report = sync_expired_members()
        report = refresh_due_class_meetings()
    except Exception as exc:
        # Do not put DB/SDK messages, records or server credentials in replies.
        logging.getLogger("uvicorn.error").error("MONTHLY_SCHEDULED_REFRESH_FAILED error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="班级月更暂不可用，请稍后重试") from None
    return {"success": report["failed"] == 0 and report["review_required"] == 0 and renewal_report['failed'] == 0, "data": report,
            "renewal_status_sync": renewal_report}


@router.post('/renewal-status-preview')
def preview_renewal_expiry(payload: RefreshPayload,
    x_study_evidence_cleanup_token: str | None = Header(default=None,alias='X-Study-Evidence-Cleanup-Token')):
    require_maintenance_token(x_study_evidence_cleanup_token)
    from app.services.renewal_expiry import sync_expired_members
    return {'success':True,'data':sync_expired_members(dry_run=True)}
