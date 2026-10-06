from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
import json
import re
from typing import Any

from app.core.settings import get_settings
from app.services.integrations import verify_integration_key
from app.services.legacy_operations_merge import (
    WORKBOOK_SOURCE_SYSTEM,
    _apply_parsed_bundle,
    _preview_parsed_bundle,
    bundle_sha256,
    parse_bundle,
)


@dataclass(frozen=True)
class ParticipationHistoryImportScope:
    bundle_sha256: str
    verified_fact_count: int
    deadline_utc: datetime


# USER-20261007-ONE-BUNDLE-HISTORY: approved frozen package only.
# Successful apply closes further writes for this SHA; the deadline is UTC.
APPROVED_IMPORT_SCOPES: tuple[ParticipationHistoryImportScope, ...] = (
    ParticipationHistoryImportScope(
        bundle_sha256="e431a047d3c91cbaa38fe8df7207527849a2abab4eaf9b8fbe8b81c438706d96",
        verified_fact_count=7582,
        deadline_utc=datetime(2026, 10, 8, 15, 59, 59, tzinfo=UTC),
    ),
)

WORKBOOK_TABLES = {
    "learning_meetings", "class_study_days", "group_sessions",
    "class_openings", "courses", "other_activities",
}
WORKBOOK_FACT_FIELDS = {
    "source_table", "external_id", "member_code", "occurred_on", "participation_status", "title",
}
WORKBOOK_PRIVACY = {
    "matching_key": "member_code", "contains_names": False,
    "contains_phones": False, "contains_narratives": False,
}


def verify_service_identity(api_key: str | None) -> None:
    settings = get_settings()
    if not settings.integration_api_key or (
        settings.is_production and settings.integration_api_key == "dev-integration-key"
    ):
        raise PermissionError("历史事实集成身份未显式配置")
    verify_integration_key(api_key or "")


def _approved_bundle(content: bytes) -> tuple[dict[str, Any], ParticipationHistoryImportScope]:
    digest = bundle_sha256(content)
    now = datetime.now(UTC)
    scopes = [scope for scope in APPROVED_IMPORT_SCOPES if scope.bundle_sha256 == digest]
    if len(scopes) != 1:
        raise PermissionError("该冻结活动包没有已批准的机器导入范围")
    scope = scopes[0]
    if (
        not re.fullmatch(r"[0-9a-f]{64}", scope.bundle_sha256)
        or type(scope.verified_fact_count) is not int
        or scope.verified_fact_count <= 0
        or scope.deadline_utc.tzinfo is None
        or scope.deadline_utc.utcoffset() != timedelta(0)
        or now >= scope.deadline_utc
    ):
        raise PermissionError("该冻结活动包的机器导入范围无效或已到期")
    bundle = parse_bundle(content)
    payload = json.loads(content.decode("utf-8-sig"))
    if (
        bundle["source_system"] != WORKBOOK_SOURCE_SYSTEM
        or payload.get("privacy_contract") != WORKBOOK_PRIVACY
        or len(bundle["facts"]) != scope.verified_fact_count
    ):
        raise ValueError("仅允许已批准数量的匿名活动工作簿事实")
    today = datetime.now(timezone(timedelta(hours=8))).date()
    for raw, fact in zip(payload["facts"], bundle["facts"]):
        if (
            set(raw) != WORKBOOK_FACT_FIELDS
            or any(not isinstance(raw[key], str) for key in WORKBOOK_FACT_FIELDS)
            or raw["participation_status"] != "PRESENT"
            or fact["source_table"] not in WORKBOOK_TABLES
            or not fact["member_code"]
            or not fact["title"]
            or re.search(r"1[3-9]\d{9}", fact["title"])
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw["occurred_on"])
        ):
            raise ValueError("仅允许明确签到的匿名学习和活动事实")
        occurred = date.fromisoformat(fact["occurred_on"])
        if occurred.year not in (2025, 2026) or occurred > today:
            raise ValueError("仅允许2025及2026年已发生的历史日期")
    return bundle, scope


def preview_workbook_bundle(content: bytes, source_name: str, api_key: str) -> dict[str, Any]:
    verify_service_identity(api_key)
    bundle, _scope = _approved_bundle(content)
    return _preview_parsed_bundle(bundle, content, source_name, require_known_codes=True)


def apply_workbook_bundle(
    content: bytes, source_name: str, api_key: str, confirmation_reason: str, second_confirmed: bool
) -> dict[str, Any]:
    verify_service_identity(api_key)
    settings = get_settings()
    if settings.deployment_read_only or (settings.is_production and not settings.allow_production_mutations):
        raise PermissionError("当前环境禁止历史活动事实写入")
    reason = confirmation_reason.strip()
    if not second_confirmed or len(reason) < 8 or len(reason) > 1000:
        raise ValueError("正式导入必须二次确认并填写8至1000个字符的原因")
    bundle, scope = _approved_bundle(content)
    return _apply_parsed_bundle(
        bundle, content, None, reason, require_known_codes=True, service_actor=True,
        service_deadline_utc=scope.deadline_utc,
    )
