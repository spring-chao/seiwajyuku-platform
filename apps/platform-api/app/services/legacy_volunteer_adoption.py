"""Read-only preview and explicitly confirmed adoption of legacy volunteer roles.

``members.class_committee_name`` is retained as a legacy source field.  This
module never upgrades it while a profile is opened or edited.  It provides a
scoped, deterministic preview first, and a separately gated apply operation
that re-validates every candidate inside one transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

from app.core.settings import get_settings
from app.db import execute, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids
from app.services.volunteer_positions import (
    CURRENT_VOLUNTEER_HIDDEN_POSITION_KEYS,
    _db_timestamp,
    _catalog_rows,
    _ensure_member_scope,
    _insert_appointment,
    _member_person,
    _write_gate,
    validate_position_target,
)


LEGACY_POSITION_AUTO_ADOPT_SOURCE = "LEGACY_POSITION_AUTO_ADOPT"
LEGACY_POSITION_AUTO_ADOPT_CONFIRMATION = "确认批量承接历史志工岗位"
LEGACY_POSITION_AUTO_ADOPT_PURPOSE = "历史岗位安全承接当前志工岗位"
LEGACY_POSITION_AUTO_ADOPT_BATCH_ACTION = (
    "members.legacy_volunteer_position.auto_adopt_batch"
)
LEGACY_POSITION_AUTO_ADOPT_ACTION = "members.legacy_volunteer_position.auto_adopt"


REASON_LABELS: dict[str, str] = {
    "MEMBER_NOT_ACTIVE": "学员不是在册状态",
    "HISTORICAL_POSITION_EMPTY": "历史岗位为空",
    "MULTIPLE_HISTORICAL_POSITIONS": "历史岗位包含多个岗位，需要人工复核",
    "HISTORICAL_POSITION_UNKNOWN": "历史岗位无法唯一匹配岗位目录",
    "HISTORICAL_POSITION_AMBIGUOUS": "岗位目录中存在多个同名岗位，无法唯一匹配",
    "HISTORICAL_POSITION_INACTIVE": "历史岗位对应目录已停用",
    "HIDDEN_POSITION_REQUIRES_REVIEW": "历史岗位属于已隐藏的汇总岗位，需要人工确认具体岗位",
    "IDENTITY_NOT_ACTIVE": "已有身份档案不是有效状态",
    "PERSON_PROFILE_NOT_ACTIVE": "已有自然人档案不是有效状态",
    "MULTIPLE_ACTIVE_APPOINTMENTS": "当前存在多个有效志工岗位，需要人工复核",
    "INVALID_ACTIVE_APPOINTMENT": "存在格式异常的有效志工任职，需要人工复核",
    "ALREADY_CURRENT_POSITION": "已经存在当前志工岗位，不覆盖",
    "NON_CURRENT_APPOINTMENT_REQUIRES_REVIEW": "存在未结束但非当前状态的志工任职，需要人工复核",
    "MISSING_FORMAL_REGION": "缺少唯一有效的正式分中心关系",
    "AMBIGUOUS_FORMAL_REGION": "存在多个有效正式分中心关系",
    "MISSING_FORMAL_CLASS": "缺少唯一有效的正式班级关系",
    "AMBIGUOUS_FORMAL_CLASS": "存在多个有效正式班级关系",
    "MISSING_FORMAL_GROUP": "缺少唯一有效的正式小组关系",
    "AMBIGUOUS_FORMAL_GROUP": "存在多个有效正式小组关系",
    "INVALID_SCOPE_TARGET": "当前正式组织关系不符合岗位服务范围要求",
    "NOT_IN_PREVIEW_SCOPE": "不在本次预览账号的组织授权范围内",
    "PREVIEW_CHANGED": "预览后资料已变化，请重新生成预览",
}


# A legacy field occasionally contains a compact list such as “辅导员、组委”.
# Exact single-role values are still matched against the catalog; only these
# explicit separators/phrases cause the multi-position safety stop.
_MULTI_POSITION_RE = re.compile(r"(?:、|,|，|/|／|;|；|\+|＋|兼任|兼|和|及)")


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return (
        parsed.replace(tzinfo=UTC)
        if parsed.tzinfo is None
        else parsed.astimezone(UTC)
    )


def _is_current_value(value: Any, *, before_or_equal: bool, now: datetime) -> bool:
    if value is None:
        return True
    parsed = _as_utc(value)
    if parsed is None:
        return False
    return parsed <= now if before_or_equal else parsed >= now


def _relation_is_current(row: dict[str, Any], now: datetime) -> bool:
    if not bool(row.get("is_active")):
        return False
    return _is_current_value(row.get("valid_from"), before_or_equal=True, now=now) and _is_current_value(
        row.get("valid_until"), before_or_equal=False, now=now
    )


def _appointment_state(row: dict[str, Any], now: datetime) -> str:
    """Return CURRENT, HISTORICAL, PENDING or INVALID for one appointment."""

    status = str(row.get("status") or "").upper()
    if status == "ACTIVE":
        starts = _as_utc(row.get("starts_at"))
        ends = _as_utc(row.get("ends_at")) if row.get("ends_at") is not None else None
        if starts is None or (
            row.get("ends_at") is not None and ends is None
        ):
            return "INVALID"
        if starts <= now and (ends is None or ends >= now):
            return "CURRENT"
        return "HISTORICAL"
    if status in {"PLANNED", "SUSPENDED"}:
        return "PENDING"
    return "HISTORICAL"


def _has_multiple_historical_positions(value: str) -> bool:
    return len(
        [part.strip() for part in _MULTI_POSITION_RE.split(value) if part.strip()]
    ) > 1


def _reason(code: str) -> tuple[str, str]:
    return code, REASON_LABELS[code]


def _load_legacy_members(connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in execute(
            connection,
            "SELECT m.id AS member_id, m.member_code, m.name, m.status, "
            "m.org_unit_id, m.class_committee_name, o.name AS primary_org_name "
            "FROM members m LEFT JOIN org_units o ON o.id=m.org_unit_id "
            "WHERE m.class_committee_name IS NOT NULL "
            "AND TRIM(m.class_committee_name)<>'' "
            "ORDER BY m.name, m.id",
        ).fetchall()
    ]


def _load_identities(connection) -> dict[int, dict[str, Any]]:
    rows = execute(
        connection,
        "SELECT mi.member_id, mi.person_id, mi.status AS identity_status, "
        "pp.status AS person_status "
        "FROM member_identities mi LEFT JOIN person_profiles pp ON pp.id=mi.person_id",
    ).fetchall()
    return {int(row["member_id"]): dict(row) for row in rows}


def _load_appointments(connection) -> dict[str, list[dict[str, Any]]]:
    rows = execute(
        connection,
        "SELECT va.id, va.person_id, va.appointment_key, va.org_unit_id, "
        "va.scope_type, va.starts_at, va.ends_at, va.status, va.source_reference, "
        "c.position_name, c.scope_level, c.is_active AS position_is_active, "
        "o.name AS scope_name "
        "FROM volunteer_appointments va "
        "LEFT JOIN volunteer_position_catalog c ON c.position_key=va.appointment_key "
        "LEFT JOIN org_units o ON o.id=va.org_unit_id "
        "ORDER BY va.person_id, va.starts_at, va.id",
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["person_id"])].append(dict(row))
    return grouped


def _load_relations(connection) -> dict[int, list[dict[str, Any]]]:
    rows = execute(
        connection,
        "SELECT r.member_id, r.org_unit_id, r.relation_type, r.is_primary, "
        "r.valid_from, r.valid_until, o.name AS org_name, o.unit_type, "
        "o.is_active "
        "FROM member_org_relations r JOIN org_units o ON o.id=r.org_unit_id "
        "ORDER BY r.member_id, r.is_primary DESC, r.id DESC",
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["member_id"])].append(dict(row))
    return grouped


def _is_visible(
    member: dict[str, Any],
    relations: list[dict[str, Any]],
    allowed_org_ids: set[str] | None,
    now: datetime,
) -> bool:
    if allowed_org_ids is None:
        return True
    if str(member.get("org_unit_id") or "") in allowed_org_ids:
        return True
    return any(
        str(relation.get("org_unit_id") or "") in allowed_org_ids
        and _relation_is_current(relation, now)
        for relation in relations
    )


def _position_match(
    historical_name: str, catalog: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, tuple[str, str] | None]:
    matches = [
        item
        for item in catalog
        if str(item.get("position_name") or "").strip() == historical_name
    ]
    active_matches = [item for item in matches if bool(item.get("is_active", True))]
    if not matches:
        return None, _reason("HISTORICAL_POSITION_UNKNOWN")
    if len(matches) > 1 or len(active_matches) > 1:
        return None, _reason("HISTORICAL_POSITION_AMBIGUOUS")
    position = active_matches[0] if active_matches else matches[0]
    if not bool(position.get("is_active", True)):
        return None, _reason("HISTORICAL_POSITION_INACTIVE")
    if position["position_key"] in CURRENT_VOLUNTEER_HIDDEN_POSITION_KEYS:
        return None, _reason("HIDDEN_POSITION_REQUIRES_REVIEW")
    return position, None


def _scope_target(
    connection,
    position: dict[str, Any],
    relations: list[dict[str, Any]],
    now: datetime,
) -> tuple[dict[str, Any] | None, tuple[str, str] | None]:
    level = str(position.get("scope_level") or "").upper()
    if level == "GROUP":
        relation_types = {"STUDY_GROUP"}
        unit_types = {"GROUP"}
        missing_code, ambiguous_code = "MISSING_FORMAL_GROUP", "AMBIGUOUS_FORMAL_GROUP"
    elif level == "CLASS":
        relation_types = {"STUDY_CLASS", "SPECIAL_COHORT"}
        unit_types = {"CLASS", "SPECIAL_COHORT"}
        missing_code, ambiguous_code = "MISSING_FORMAL_CLASS", "AMBIGUOUS_FORMAL_CLASS"
    elif level in {"REGIONAL_CENTER", "ANY"}:
        relation_types = {"PRIMARY_REGION"}
        unit_types = {"REGIONAL_CENTER"}
        missing_code, ambiguous_code = "MISSING_FORMAL_REGION", "AMBIGUOUS_FORMAL_REGION"
    else:
        return None, _reason("INVALID_SCOPE_TARGET")

    candidates = [
        row
        for row in relations
        if row.get("relation_type") in relation_types
        and row.get("unit_type") in unit_types
        and _relation_is_current(row, now)
    ]
    if not candidates:
        return None, _reason(missing_code)
    if len(candidates) > 1:
        return None, _reason(ambiguous_code)
    candidate = candidates[0]
    try:
        target = validate_position_target(
            connection,
            position_key=position["position_key"],
            org_unit_id=str(candidate["org_unit_id"]),
            scope_type="UNIT",
        )
    except ValueError:
        return None, _reason("INVALID_SCOPE_TARGET")
    return {
        "org_unit_id": str(candidate["org_unit_id"]),
        "org_name": candidate.get("org_name"),
        "org_unit_type": candidate.get("unit_type"),
        "scope_type": target["scope_type"],
    }, None


def _current_appointment_data(
    identity: dict[str, Any] | None,
    appointments_by_person: dict[str, list[dict[str, Any]]],
    now: datetime,
) -> tuple[list[dict[str, Any]], bool, list[dict[str, Any]]]:
    if not identity or not identity.get("person_id"):
        return [], False, []
    rows = appointments_by_person.get(str(identity["person_id"]), [])
    current: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for row in rows:
        state = _appointment_state(row, now)
        if state == "CURRENT":
            current.append(row)
        elif state == "INVALID":
            invalid.append(row)
        elif state == "PENDING":
            pending.append(row)
    return current, bool(invalid), pending


def _current_appointment_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "appointment_id": int(row["id"]),
        "position_key": row.get("appointment_key"),
        "position_name": row.get("position_name") or row.get("appointment_key"),
        "scope_org_unit_id": row.get("org_unit_id"),
        "scope_name": row.get("scope_name"),
        "source_reference": row.get("source_reference"),
    }


def _build_item(
    connection,
    member: dict[str, Any],
    *,
    catalog: list[dict[str, Any]],
    identities: dict[int, dict[str, Any]],
    appointments_by_person: dict[str, list[dict[str, Any]]],
    relations_by_member: dict[int, list[dict[str, Any]]],
    now: datetime,
) -> dict[str, Any]:
    member_id = int(member["member_id"])
    historical_name = str(member.get("class_committee_name") or "").strip()
    item: dict[str, Any] = {
        "member_id": member_id,
        "member_code": member.get("member_code"),
        "name": member.get("name"),
        "member_status": member.get("status"),
        "historical_position_name": historical_name,
        "position_key": None,
        "position_name": None,
        "scope_level": None,
        "scope": None,
        "current_appointment": None,
        "auto_adoptable": False,
        "reason_code": None,
        "reason": None,
    }

    def stop(code: str) -> dict[str, Any]:
        item["reason_code"], item["reason"] = _reason(code)
        return item

    if not historical_name:
        return stop("HISTORICAL_POSITION_EMPTY")
    if member.get("status") != "ACTIVE":
        return stop("MEMBER_NOT_ACTIVE")
    if _has_multiple_historical_positions(historical_name):
        return stop("MULTIPLE_HISTORICAL_POSITIONS")

    position, position_error = _position_match(historical_name, catalog)
    if position_error:
        item["reason_code"], item["reason"] = position_error
        return item
    assert position is not None
    item.update(
        {
            "position_key": position["position_key"],
            "position_name": position["position_name"],
            "scope_level": position["scope_level"],
        }
    )

    identity = identities.get(member_id)
    if identity:
        if identity.get("identity_status") != "ACTIVE":
            return stop("IDENTITY_NOT_ACTIVE")
        if identity.get("person_status") != "ACTIVE":
            return stop("PERSON_PROFILE_NOT_ACTIVE")
    current, invalid_active, pending = _current_appointment_data(
        identity, appointments_by_person, now
    )
    if invalid_active:
        return stop("INVALID_ACTIVE_APPOINTMENT")
    if len(current) > 1:
        item["current_appointment"] = [
            _current_appointment_summary(row) for row in current
        ]
        return stop("MULTIPLE_ACTIVE_APPOINTMENTS")
    if len(current) == 1:
        item["current_appointment"] = _current_appointment_summary(current[0])
        return stop("ALREADY_CURRENT_POSITION")
    if pending:
        item["current_appointment"] = [
            _current_appointment_summary(row) for row in pending
        ]
        return stop("NON_CURRENT_APPOINTMENT_REQUIRES_REVIEW")

    target, target_error = _scope_target(
        connection, position, relations_by_member.get(member_id, []), now
    )
    if target_error:
        item["reason_code"], item["reason"] = target_error
        return item
    item["scope"] = {
        "scope_type": target["scope_type"],
        "scope_org_unit_id": target["org_unit_id"],
        "scope_name": target["org_name"],
        "org_unit_type": target["org_unit_type"],
    }
    item["auto_adoptable"] = True
    return item


def _fingerprint(items: list[dict[str, Any]]) -> str:
    payload = [
        {
            "member_id": int(item["member_id"]),
            "historical_position_name": item["historical_position_name"],
            "position_key": item["position_key"],
            "scope_level": item["scope_level"],
            "scope_type": item["scope"].get("scope_type") if item.get("scope") else None,
            "scope_org_unit_id": (
                item["scope"].get("scope_org_unit_id") if item.get("scope") else None
            ),
        }
        for item in items
        if item.get("auto_adoptable")
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _preview_in_connection(
    connection,
    *,
    actor_user_id: int | None,
    now: datetime,
) -> dict[str, Any]:
    members = _load_legacy_members(connection)
    allowed = accessible_org_ids(actor_user_id) if actor_user_id is not None else None
    relations_by_member = _load_relations(connection)
    visible_members = [
        member
        for member in members
        if _is_visible(
            member,
            relations_by_member.get(int(member["member_id"]), []),
            allowed,
            now,
        )
    ]
    catalog = _catalog_rows(connection, active_only=False)
    identities = _load_identities(connection)
    appointments_by_person = _load_appointments(connection)
    items = [
        _build_item(
            connection,
            member,
            catalog=catalog,
            identities=identities,
            appointments_by_person=appointments_by_person,
            relations_by_member=relations_by_member,
            now=now,
        )
        for member in visible_members
    ]
    adoptable = [item for item in items if item["auto_adoptable"]]
    manual_review = [item for item in items if not item["auto_adoptable"]]
    position_stats: dict[str, dict[str, Any]] = {}
    reasons = Counter()
    for item in items:
        key = item["historical_position_name"]
        stats = position_stats.setdefault(
            key,
            {
                "historical_position_name": key,
                "position_key": item.get("position_key"),
                "position_name": item.get("position_name"),
                "total_count": 0,
                "auto_adoptable_count": 0,
                "manual_review_count": 0,
            },
        )
        stats["total_count"] += 1
        if item["auto_adoptable"]:
            stats["auto_adoptable_count"] += 1
        else:
            stats["manual_review_count"] += 1
            if item.get("reason_code"):
                reasons[item["reason_code"]] += 1
    sorted_stats = sorted(
        position_stats.values(),
        key=lambda item: (
            str(item.get("position_name") or item["historical_position_name"]),
            item["historical_position_name"],
        ),
    )
    return {
        "mode": "READ_ONLY_PREVIEW",
        "no_write": True,
        "environment": get_settings().app_env,
        "source_field": "members.class_committee_name",
        "source_reference": LEGACY_POSITION_AUTO_ADOPT_SOURCE,
        "generated_at": now.isoformat(),
        "preview_total": len(items),
        "auto_adoptable_count": len(adoptable),
        "manual_review_count": len(manual_review),
        "adoptable_member_ids": [int(item["member_id"]) for item in adoptable],
        "by_position": sorted_stats,
        "reason_counts": [
            {"reason_code": code, "reason": REASON_LABELS[code], "count": count}
            for code, count in sorted(reasons.items())
        ],
        "auto_adoptable_items": adoptable,
        "manual_review_items": manual_review,
        "preview_fingerprint": _fingerprint(items),
    }


def preview_legacy_volunteer_adoption(
    actor_user_id: int | None = None, *, connection=None
) -> dict[str, Any]:
    """Return a complete scoped dry-run without persisting preview data."""

    _write_gate()
    context = nullcontext(connection) if connection is not None else transaction()
    with context as current_connection:
        return _preview_in_connection(
            current_connection, actor_user_id=actor_user_id, now=_now()
        )


def _is_idempotently_adopted(item: dict[str, Any]) -> bool:
    current = item.get("current_appointment")
    if not isinstance(current, dict):
        return False
    return bool(
        current.get("source_reference") == LEGACY_POSITION_AUTO_ADOPT_SOURCE
        and current.get("position_key") == item.get("position_key")
    )


def apply_legacy_volunteer_adoption(
    actor_user_id: int,
    *,
    preview_fingerprint: str,
    member_ids: list[int],
    confirmation: str,
    connection=None,
) -> dict[str, Any]:
    """Apply only the explicitly previewed candidates after a confirmation.

    The production write gate is checked before any transaction.  Repeating the
    same request after a successful apply is safe: already-created records are
    reported as idempotent skips rather than duplicated.
    """

    _write_gate(write=True)
    if confirmation.strip() != LEGACY_POSITION_AUTO_ADOPT_CONFIRMATION:
        raise ValueError("批量承接确认文字不正确，已禁止写入")
    normalized_fingerprint = preview_fingerprint.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized_fingerprint):
        raise ValueError("预览指纹无效")
    normalized_ids = sorted({int(member_id) for member_id in member_ids})
    if not normalized_ids:
        raise ValueError("至少选择一名可承接学员")
    if any(member_id <= 0 for member_id in normalized_ids):
        raise ValueError("承接学员编号无效")
    if len(normalized_ids) > 10000:
        raise ValueError("单次批量承接不得超过10000人")

    context = nullcontext(connection) if connection is not None else transaction()
    with context as current_connection:
        preview = _preview_in_connection(
            current_connection, actor_user_id=actor_user_id, now=_now()
        )
        items_by_id = {
            int(item["member_id"]): item
            for item in preview["auto_adoptable_items"] + preview["manual_review_items"]
        }
        current_fingerprint = preview["preview_fingerprint"]
        requested_items = [items_by_id.get(member_id) for member_id in normalized_ids]
        missing = [
            member_id
            for member_id, item in zip(normalized_ids, requested_items)
            if item is None
        ]
        if missing:
            raise ValueError(f"学员不在当前预览范围内：{','.join(map(str, missing))}")
        if normalized_fingerprint != current_fingerprint and not all(
            _is_idempotently_adopted(item) for item in requested_items if item
        ):
            raise ValueError("预览已变化，请重新生成预览后再承接")

        adopted: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for member_id, item in zip(normalized_ids, requested_items):
            assert item is not None
            if _is_idempotently_adopted(item):
                skipped.append(
                    {
                        "member_id": member_id,
                        "status": "IDEMPOTENT_SKIP",
                        "reason_code": "ALREADY_CURRENT_POSITION",
                        "reason": "该历史岗位已完成自动承接，本次不重复创建任职",
                    }
                )
                continue
            if not item["auto_adoptable"]:
                skipped.append(
                    {
                        "member_id": member_id,
                        "status": "SKIPPED",
                        "reason_code": item.get("reason_code"),
                        "reason": item.get("reason"),
                    }
                )
                continue

            member = execute(
                current_connection,
                "SELECT id, name, org_unit_id, status FROM members WHERE id=?",
                (member_id,),
            ).fetchone()
            if not member:
                raise ValueError("学员不存在")
            member_dict = dict(member)
            if member_dict.get("status") != "ACTIVE":
                raise ValueError("预览后学员状态已变化，请重新生成预览")
            _ensure_member_scope(actor_user_id, member_dict)
            target = item["scope"]
            assert target is not None
            person_id = _member_person(
                current_connection,
                member_id,
                actor_user_id=actor_user_id,
                source=LEGACY_POSITION_AUTO_ADOPT_SOURCE,
                audit_purpose="历史岗位自动承接正式身份档案",
            )
            starts_at = _db_timestamp(current_connection)
            appointment_id = _insert_appointment(
                current_connection,
                person_id=person_id,
                member_id=member_id,
                actor_user_id=actor_user_id,
                position_key=str(item["position_key"]),
                org_unit_id=str(target["scope_org_unit_id"]),
                scope_type=str(target["scope_type"]),
                starts_at=starts_at,
                ends_at=None,
                source_reference=LEGACY_POSITION_AUTO_ADOPT_SOURCE,
                confirmation_note=LEGACY_POSITION_AUTO_ADOPT_PURPOSE,
            )
            after = {
                "member_id": member_id,
                "appointment_id": appointment_id,
                "position_key": item["position_key"],
                "position_name": item["position_name"],
                "scope_level": item["scope_level"],
                "scope_type": target["scope_type"],
                "scope_org_unit_id": target["scope_org_unit_id"],
                "scope_name": target["scope_name"],
                "source_reference": LEGACY_POSITION_AUTO_ADOPT_SOURCE,
                "starts_at": starts_at,
                "ends_at": None,
                "starts_at_semantics": "SYSTEM_CONFIRMATION_TIME_ONLY",
            }
            write_audit(
                current_connection,
                actor_user_id=actor_user_id,
                action=LEGACY_POSITION_AUTO_ADOPT_ACTION,
                resource_type="member",
                resource_id=str(member_id),
                org_unit_id=str(target["scope_org_unit_id"]),
                purpose=LEGACY_POSITION_AUTO_ADOPT_PURPOSE,
                before={
                    "historical_position_name": item["historical_position_name"],
                    "current_volunteer_position": None,
                },
                after=after,
            )
            adopted.append(
                {
                    "member_id": member_id,
                    "status": "ADOPTED",
                    "appointment_id": appointment_id,
                    "position_key": item["position_key"],
                    "position_name": item["position_name"],
                    "scope_org_unit_id": target["scope_org_unit_id"],
                    "scope_name": target["scope_name"],
                }
            )

        write_audit(
            current_connection,
            actor_user_id=actor_user_id,
            action=LEGACY_POSITION_AUTO_ADOPT_BATCH_ACTION,
            resource_type="legacy_volunteer_adoption_batch",
            resource_id=normalized_fingerprint,
            purpose=LEGACY_POSITION_AUTO_ADOPT_PURPOSE,
            before={
                "preview_fingerprint": normalized_fingerprint,
                "requested_member_count": len(normalized_ids),
            },
            after={
                "current_preview_fingerprint": current_fingerprint,
                "adopted_count": len(adopted),
                "skipped_count": len(skipped),
                "source_reference": LEGACY_POSITION_AUTO_ADOPT_SOURCE,
            },
        )
        return {
            "mode": "APPLIED",
            "preview_fingerprint": normalized_fingerprint,
            "current_preview_fingerprint": current_fingerprint,
            "requested_count": len(normalized_ids),
            "adopted_count": len(adopted),
            "skipped_count": len(skipped),
            "adopted": adopted,
            "skipped": skipped,
        }
