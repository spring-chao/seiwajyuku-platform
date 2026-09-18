from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import require_permission
from app.core.settings import get_settings
from app.services.production_operations import (
    ProductionOperationError,
    REQUIRED_PERMISSION,
    apply_g5_4_course_rule_reconciliation,
)


router = APIRouter(prefix="/api/v1/ops/production-actions", tags=["production-actions"])


class G54CourseRuleApplyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_release_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    expected_production_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_canonical_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_course_rule_version_id: int = Field(gt=0)
    expected_course_rule_status: Literal["DRAFT"]
    expected_rule_count: Literal[14]
    expected_placeholder_keys: list[str] = Field(min_length=3, max_length=3)
    execution_reason: str = Field(min_length=8, max_length=1000)


def _require_feature_enabled() -> None:
    if not get_settings().g5_4_production_rule_apply_enabled:
        raise HTTPException(404, {"code": "FEATURE_DISABLED", "message": "Not found"})


@router.post(
    "/g5-4-course-rule-reconciliation/apply",
    include_in_schema=False,
)
def apply_g5_4_course_rules(
    payload: G54CourseRuleApplyPayload,
    _: None = Depends(_require_feature_enabled),
    actor: dict = Depends(require_permission(REQUIRED_PERMISSION)),
) -> dict:
    if "system_admin" not in actor.get("roles", []):
        raise HTTPException(403, {"code": "PERMISSION_DENIED", "message": "无此操作权限"})
    try:
        result = apply_g5_4_course_rule_reconciliation(
            **payload.model_dump(),
            actor_user_id=int(actor["id"]),
        )
    except ProductionOperationError as exc:
        raise HTTPException(
            exc.status_code,
            {"code": exc.code, "message": exc.message},
        ) from exc
    return {"success": True, "data": result}
