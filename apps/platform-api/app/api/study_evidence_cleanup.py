"""Private server-to-server entry point for bounded evidence cleanup."""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core.settings import get_settings
from app.services.study_meeting_evidence import cleanup_evidence
from app.services.study_meetings import StudyMeetingError, StudyMeetingPermissionError


router = APIRouter(prefix="/api/v1/internal/study-evidence", tags=["internal-maintenance"])


class CleanupPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=500, ge=1, le=500)


def _require_cleanup_token(token: str | None) -> None:
    """Require a high-entropy server-side token without revealing its value."""

    expected = get_settings().study_evidence_cleanup_token
    if len(expected) < 32 or not token or not hmac.compare_digest(token, expected):
        # Do not distinguish an absent configuration from a bad caller token.
        raise HTTPException(status_code=401, detail="清理服务身份无效")


@router.post("")
def cleanup_study_evidence(
    payload: CleanupPayload,
    x_study_evidence_cleanup_token: str | None = Header(
        default=None, alias="X-Study-Evidence-Cleanup-Token"
    ),
) -> dict:
    """Run one bounded cleanup batch for the CloudBase timer function.

    This endpoint deliberately has no browser/user-auth path.  It is only
    callable by the server-side scheduler holding the separately configured
    token; the cleanup service remains the single owner of DB/object rules.
    """

    _require_cleanup_token(x_study_evidence_cleanup_token)
    try:
        report = cleanup_evidence(apply=True, limit=payload.limit)
    except StudyMeetingPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except StudyMeetingError as exc:
        raise HTTPException(status_code=503, detail="合影清理暂不可用，请稍后重试") from exc
    except Exception:  # pragma: no cover - defensive boundary for SDK/DB faults
        raise HTTPException(status_code=503, detail="合影清理暂不可用，请稍后重试")
    return {"success": True, "data": report}
