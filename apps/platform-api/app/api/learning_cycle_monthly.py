"""Bounded scheduler entry point; it exposes only aggregate monthly counts."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from app.api.study_evidence_cleanup import require_maintenance_token
from app.services import learning_cycle_monthly as monthly

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
        report = monthly.refresh_sweep()
    except Exception as exc:
        # Do not put DB/SDK messages, records or server credentials in replies.
        logging.getLogger("uvicorn.error").error("MONTHLY_SCHEDULED_REFRESH_FAILED error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="班级月更暂不可用，请稍后重试") from None
    return {"success": report["failed"] == 0, "data": report}
