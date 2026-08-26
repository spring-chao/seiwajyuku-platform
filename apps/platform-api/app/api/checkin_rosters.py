"""checkin-rosters API - endpoints for the signin system to fetch roster data.

These endpoints are called by the signin system (cloud function) to get
class/group/special_cohort member lists for checkin activities.

Authentication: X-API-Key header (SIGNIN_SERVICE_API_KEY).
The members endpoint returns a scoped phone number only for check-in matching
and records an audit event without storing the phone value.
"""
from __future__ import annotations

import hmac
from fastapi import APIRouter, Header, HTTPException, Query

from app.core.settings import get_settings
from app.db import transaction
from app.services.audit import write_audit
from app.services.checkin_rosters import (
    cross_class_members,
    roster_integrity_summary,
    roster_members,
    roster_options,
)


router = APIRouter(prefix="/api/v1/checkin-rosters", tags=["checkin-rosters"])


def _verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Verify machine-to-machine API key."""
    if not x_api_key:
        raise HTTPException(401, "缺少 X-API-Key 请求头")
    expected = get_settings().signin_service_api_key
    if not expected:
        raise HTTPException(503, "签到服务密钥未配置")
    if not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(403, "API Key 无效")


@router.get("/options")
def options(
    x_api_key: str | None = Header(default=None),
) -> dict:
    """Return class/group/special_cohort options for roster selection.

    Machine-to-machine: use X-API-Key header.
    """
    _verify_api_key(x_api_key)
    data = roster_options(0)  # 0 = system access via API key
    return {"success": True, "data": data}


@router.get("/validate")
def validate(
    x_api_key: str | None = Header(default=None),
) -> dict:
    """Return aggregate roster-source and organization integrity checks."""
    _verify_api_key(x_api_key)
    return {"success": True, "data": roster_integrity_summary()}


@router.get("/members")
def members(
    org_unit_id: str | None = Query(default=None),
    class_org_unit_id: str | None = Query(default=None),
    group_org_unit_id: str | None = Query(default=None),
    special_cohort_org_unit_id: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict:
    """Return roster members for a given class/group/special_cohort.

    Machine-to-machine: use X-API-Key header.
    Returns phone only inside the explicitly requested roster scope.
    """
    _verify_api_key(x_api_key)
    try:
        data = roster_members(
            0,  # system access via API key
            org_unit_id=org_unit_id,
            class_org_unit_id=class_org_unit_id,
            group_org_unit_id=group_org_unit_id,
            special_cohort_org_unit_id=special_cohort_org_unit_id,
            include_phone=True,  # Machine-to-machine: signin system needs phone for matching
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    scope_id = (
        group_org_unit_id
        or class_org_unit_id
        or special_cohort_org_unit_id
        or org_unit_id
    )
    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=None,
            action="integrations.checkin_roster.phone_access",
            resource_type="checkin_roster",
            resource_id=scope_id,
            org_unit_id=scope_id,
            purpose="签到系统名单匹配",
            after={"row_count": len(data), "fields": ["member_code", "phone"]},
        )
    scope_type = (
        "STUDY_GROUP"
        if group_org_unit_id
        else "STUDY_CLASS"
        if class_org_unit_id
        else "SPECIAL_COHORT"
        if special_cohort_org_unit_id
        else "PRIMARY_REGION"
    )
    return {
        "success": True,
        "data": {
            "members": data,
            "member_count": len(data),
            "source": "PLATFORM_ORG_RELATIONS",
            "query_mode": "ORG_UNIT_ID",
            "fallback_mode": "FAIL_CLOSED",
            "scope": {
                "relation_type": scope_type,
                "org_unit_id": scope_id,
                "class_org_unit_id": class_org_unit_id,
            },
        },
    }


@router.get("/cross-class-members")
def cross_class_member_lookup(
    name: str = Query(min_length=1, max_length=50),
    event_class_org_unit_id: str = Query(min_length=1, max_length=100),
    x_api_key: str | None = Header(default=None),
) -> dict:
    """Return exact-name active members whose current class differs from the event.

    This service-to-service endpoint supports public check-in after an exact
    name is absent from the selected class roster. It never returns a phone
    number and records only aggregate query metadata in the audit trail.
    """
    _verify_api_key(x_api_key)
    try:
        data = cross_class_members(
            name=name,
            event_class_org_unit_id=event_class_org_unit_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=None,
            action="integrations.checkin_roster.cross_class_lookup",
            resource_type="checkin_roster",
            resource_id=event_class_org_unit_id,
            org_unit_id=event_class_org_unit_id,
            purpose="跨班学习签到身份定位",
            after={
                "query_mode": "EXACT_NAME_CURRENT_STUDY_CLASS",
                "match_count": len(data),
                "phone_included": False,
            },
        )
    return {
        "success": True,
        "data": {
            "members": data,
            "member_count": len(data),
            "source": "PLATFORM_ORG_RELATIONS",
            "query_mode": "EXACT_NAME_CURRENT_STUDY_CLASS",
            "fallback_mode": "FAIL_CLOSED",
            "event_class_org_unit_id": event_class_org_unit_id,
        },
    }
