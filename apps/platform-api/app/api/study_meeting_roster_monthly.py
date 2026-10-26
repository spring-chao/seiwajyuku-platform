"""Server-clock-only, authenticated monthly report attribution refresh."""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict
from app.api.study_evidence_cleanup import require_maintenance_token

router = APIRouter(prefix="/api/v1/internal/study-meeting-roster-monthly",tags=["internal-maintenance"])

class RefreshPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

@router.post("")
def refresh(payload: RefreshPayload,x_study_evidence_cleanup_token: str | None = Header(default=None,alias="X-Study-Evidence-Cleanup-Token")):
    require_maintenance_token(x_study_evidence_cleanup_token)
    from app.services.study_meeting_monthly_roster import refresh_current_month
    try:
        report=refresh_current_month()
    except Exception:
        raise HTTPException(503,"月度统计归组更新暂未完成，请稍后重试") from None
    return {"success":report['failed']==0,"data":report}
