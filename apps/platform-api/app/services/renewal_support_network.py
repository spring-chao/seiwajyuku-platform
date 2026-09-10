from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids


SUPPORT_ROLE_META: dict[str, dict[str, Any]] = {
    "REFERRER": {
        "label": "推荐人",
        "priority": 1,
        "reason": "推荐人通常最了解入塾缘起，适合先了解真实想法与关切。",
    },
    "GROUP_LEADER": {
        "label": "组长",
        "priority": 2,
        "reason": "同组日常关系更近，适合先通过熟悉关系了解近况。",
    },
    "GROUP_COUNSELOR": {
        "label": "辅导员",
        "priority": 3,
        "reason": "辅导员了解小组学习与关怀节奏，适合协同支持。",
    },
    "CLASS_TEACHER": {
        "label": "班主任",
        "priority": 4,
        "reason": "班主任掌握班级学习关系，可在合适时机协同关爱。",
    },
    "DEPUTY_CLASS_TEACHER": {
        "label": "副班主任",
        "priority": 4,
        "reason": "副班主任掌握班级学习关系，可在合适时机协同关爱。",
    },
    "CLASS_DEVELOPMENT": {
        "label": "班级发展委",
        "priority": 5,
        "reason": "班级发展建设线可在不打扰学长的前提下提供协同支持。",
    },
    "CENTER_DEVELOPMENT": {
        "label": "分中心发展委",
        "priority": 6,
        "reason": "分中心发展建设线作为较后一层关系支持，宜由运营人员审慎选择。",
    },
}
ROLE_COLLECTIONS = {
    "referrers": ("REFERRER",),
    "group_leaders": ("GROUP_LEADER",),
    "group_counselors": ("GROUP_COUNSELOR",),
    "class_teachers": ("CLASS_TEACHER", "DEPUTY_CLASS_TEACHER"),
    "class_development": ("CLASS_DEVELOPMENT",),
    "center_development": ("CENTER_DEVELOPMENT",),
}
SUPPORT_REQUEST_STATUSES = frozenset(
    {"REQUESTED", "FEEDBACK_RECEIVED", "CLOSED", "CANCELLED"}
)
SUPPORT_STATUS_META = {
    "NONE": "—",
    "PENDING": "待助力",
    "IN_PROGRESS": "助力中",
    "FEEDBACK_RECEIVED": "已有反馈",
}
CLOSED_CYCLE_STATUSES = frozenset({"RENEWED", "NOT_RENEWING", "EXITED"})
_PHONE_PATTERN = re.compile(r"(?<!\d)1\d{10}(?!\d)")
_UNSET = object()


def _now_date_text() -> str:
    return datetime.now(UTC).date().isoformat()


def _now_text() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _redact_text(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    return _PHONE_PATTERN.sub("***", text)


def _role_label(role: str) -> str:
    return SUPPORT_ROLE_META.get(role, {}).get("label", role)


def _supporter_identity(item: Mapping[str, Any]) -> str:
    if item.get("member_id") is not None:
        return f"member:{int(item['member_id'])}"
    if item.get("person_id"):
        return f"person:{item['person_id']}"
    return f"unresolved:{_text(item.get('name')).casefold()}"


def _member_scope(member_id: int, primary_org_unit_id: str, actor_user_id: int) -> set[str] | None:
    """Enforce the existing formal member-scope rule without a module cycle.

    ``members`` imports renewal helpers during application startup, so this
    import deliberately stays inside the request-time helper.
    """

    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None:
        from app.services.members import resolve_member_scope

        resolve_member_scope(member_id, primary_org_unit_id, allowed)
    return allowed


def _current_member_context(member_id: int, actor_user_id: int) -> dict[str, Any]:
    member = fetch_one(
        "SELECT m.id, m.name, m.status, m.org_unit_id, m.referrer, m.referrer_center, "
        "o.name AS org_name, o.is_active AS org_active "
        "FROM members m JOIN org_units o ON o.id=m.org_unit_id WHERE m.id=?",
        (member_id,),
    )
    if not member:
        raise ValueError("学长不存在")
    member = dict(member)
    allowed = _member_scope(member_id, member["org_unit_id"], actor_user_id)
    today = _now_date_text()
    relations = fetch_all(
        "SELECT mor.id, mor.relation_type, mor.org_unit_id, mor.is_primary, "
        "ou.name AS org_name, ou.unit_type "
        "FROM member_org_relations mor JOIN org_units ou ON ou.id=mor.org_unit_id "
        "WHERE mor.member_id=? "
        "AND mor.relation_type IN ('STUDY_GROUP','STUDY_CLASS','SPECIAL_COHORT') "
        "AND ou.is_active=1 "
        "AND (mor.valid_from IS NULL OR SUBSTR(mor.valid_from, 1, 10)<=?) "
        "AND (mor.valid_until IS NULL OR SUBSTR(mor.valid_until, 1, 10)>=?) "
        "ORDER BY mor.is_primary DESC, mor.id DESC",
        (member_id, today, today),
    )
    if allowed is not None:
        relations = [row for row in relations if row["org_unit_id"] in allowed]

    groups = [dict(row) for row in relations if row["relation_type"] == "STUDY_GROUP"]
    classes = [
        dict(row)
        for row in relations
        if row["relation_type"] in {"STUDY_CLASS", "SPECIAL_COHORT"}
    ]
    center_visible = allowed is None or member["org_unit_id"] in allowed
    return {
        "member": member,
        "allowed_org_ids": allowed,
        "center": (
            {
                "id": member["org_unit_id"],
                "name": member["org_name"],
                "is_active": bool(member["org_active"]),
            }
            if center_visible and member["org_active"]
            else None
        ),
        "groups": groups,
        "classes": classes,
    }


def _supporter_entry(
    row: Mapping[str, Any],
    *,
    role: str,
) -> dict[str, Any]:
    return {
        "role": role,
        "role_label": _role_label(role),
        "name": row["member_name"],
        "member_id": int(row["member_id"]) if row.get("member_id") is not None else None,
        "person_id": row.get("person_id"),
        "resolved": True,
        "org_unit_id": row.get("service_target_org_unit_id"),
        "org_name": row.get("service_target_name"),
        "position_key": row.get("appointment_key"),
        "position_name": row.get("position_name") or row.get("appointment_key"),
    }


def _dedupe_role_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_identity: dict[str, dict[str, Any]] = {}
    for entry in entries:
        identity = _supporter_identity(entry)
        existing = by_identity.get(identity)
        if not existing:
            by_identity[identity] = entry
            continue
        position_keys = {
            value
            for value in (existing.get("position_key"), entry.get("position_key"))
            if value
        }
        existing["position_keys"] = sorted(position_keys)
    return sorted(
        by_identity.values(),
        key=lambda item: (
            _text(item.get("name")),
            int(item.get("member_id") or 0),
        ),
    )


def _appointment_rows(
    *,
    target_org_unit_ids: Iterable[str],
    position_keys: Iterable[str] | None = None,
    system_type: str | None = None,
    line_type: str | None = None,
) -> list[dict[str, Any]]:
    targets = sorted({value for value in target_org_unit_ids if value})
    if not targets:
        return []
    conditions = [
        "va.member_id IS NOT NULL",
        "va.status='ACTIVE'",
        "supporter.status='ACTIVE'",
        "(va.starts_at IS NULL OR SUBSTR(va.starts_at, 1, 10)<=?)",
        "(va.ends_at IS NULL OR SUBSTR(va.ends_at, 1, 10)>=?)",
        "(va.volunteer_service_unit_id IS NULL OR vsu.is_active=1)",
        "COALESCE(vsu.service_target_org_unit_id, va.org_unit_id) IN ("
        + ",".join("?" for _ in targets)
        + ")",
    ]
    params: list[Any] = [_now_date_text(), _now_date_text(), *targets]
    keys = sorted({value for value in (position_keys or []) if value})
    if keys:
        conditions.append("va.appointment_key IN (" + ",".join("?" for _ in keys) + ")")
        params.extend(keys)
    if system_type:
        conditions.append("va.volunteer_service_unit_id IS NOT NULL AND vsu.system_type=?")
        params.append(system_type)
    if line_type:
        conditions.append("va.volunteer_service_unit_id IS NOT NULL AND vsu.line_type=?")
        params.append(line_type)
    rows = fetch_all(
        "SELECT va.id, va.member_id, va.person_id, va.appointment_key, "
        "supporter.name AS member_name, catalog.position_name, "
        "COALESCE(vsu.service_target_org_unit_id, va.org_unit_id) AS service_target_org_unit_id, "
        "target.name AS service_target_name "
        "FROM volunteer_appointments va "
        "JOIN members supporter ON supporter.id=va.member_id "
        "LEFT JOIN volunteer_service_units vsu ON vsu.id=va.volunteer_service_unit_id "
        "LEFT JOIN volunteer_position_catalog catalog ON catalog.position_key=va.appointment_key "
        "LEFT JOIN org_units target ON target.id=COALESCE(vsu.service_target_org_unit_id, va.org_unit_id) "
        "WHERE "
        + " AND ".join(conditions)
        + " ORDER BY catalog.sort_order, supporter.name, va.id",
        tuple(params),
    )
    return [dict(row) for row in rows]


def _referrer_entry(context: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str]:
    member = context["member"]
    name = _text(member.get("referrer"))
    if not name:
        return [], "NOT_PROVIDED"
    conditions = ["m.name=?", "m.status='ACTIVE'"]
    params: list[Any] = [name]
    center_hint = _text(member.get("referrer_center"))
    if center_hint:
        conditions.append("(o.name=? OR o.unit_code=? OR o.id=?)")
        params.extend([center_hint, center_hint, center_hint])
    allowed = context["allowed_org_ids"]
    if allowed is not None:
        if not allowed:
            return [
                {
                    "role": "REFERRER",
                    "role_label": _role_label("REFERRER"),
                    "name": name,
                    "member_id": None,
                    "person_id": None,
                    "resolved": False,
                    "org_unit_id": None,
                    "org_name": None,
                    "position_key": None,
                    "position_name": None,
                }
            ], "OUT_OF_SCOPE"
        values = sorted(allowed)
        conditions.append("m.org_unit_id IN (" + ",".join("?" for _ in values) + ")")
        params.extend(values)
    candidates = fetch_all(
        "SELECT m.id AS member_id, m.name, m.org_unit_id, o.name AS org_name, "
        "mi.person_id FROM members m JOIN org_units o ON o.id=m.org_unit_id "
        "LEFT JOIN member_identities mi ON mi.member_id=m.id AND mi.status='ACTIVE' "
        "WHERE " + " AND ".join(conditions) + " ORDER BY m.id",
        tuple(params),
    )
    if len(candidates) == 1:
        candidate = candidates[0]
        return [
            {
                "role": "REFERRER",
                "role_label": _role_label("REFERRER"),
                "name": candidate["name"],
                "member_id": int(candidate["member_id"]),
                "person_id": candidate.get("person_id"),
                "resolved": True,
                "org_unit_id": candidate["org_unit_id"],
                "org_name": candidate["org_name"],
                "position_key": None,
                "position_name": None,
            }
        ], "RESOLVED"
    return [
        {
            "role": "REFERRER",
            "role_label": _role_label("REFERRER"),
            "name": name,
            "member_id": None,
            "person_id": None,
            "resolved": False,
            "org_unit_id": None,
            "org_name": None,
            "position_key": None,
            "position_name": None,
        }
    ], "AMBIGUOUS" if candidates else "UNRESOLVED"


def _recommended_supporters(roles: Mapping[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    entries = [
        entry
        for collection in ROLE_COLLECTIONS
        for entry in roles[collection]
    ]
    entries.sort(
        key=lambda item: (
            SUPPORT_ROLE_META[item["role"]]["priority"],
            _text(item.get("name")),
        )
    )
    combined: dict[str, dict[str, Any]] = {}
    for entry in entries:
        key = _supporter_identity(entry)
        role = entry["role"]
        existing = combined.get(key)
        if not existing:
            combined[key] = {
                "name": entry["name"],
                "member_id": entry.get("member_id"),
                "person_id": entry.get("person_id"),
                "resolved": bool(entry.get("resolved")),
                "org_unit_id": entry.get("org_unit_id"),
                "org_name": entry.get("org_name"),
                "roles": [role],
                "role_labels": [_role_label(role)],
                "primary_role": role,
                "primary_role_label": _role_label(role),
                "priority": SUPPORT_ROLE_META[role]["priority"],
                "recommendation_reason": SUPPORT_ROLE_META[role]["reason"],
            }
            continue
        if role not in existing["roles"]:
            existing["roles"].append(role)
            existing["role_labels"].append(_role_label(role))
    return sorted(
        combined.values(),
        key=lambda item: (item["priority"], _text(item.get("name"))),
    )


def build_renewal_support_network(member_id: int, actor_user_id: int) -> dict[str, Any]:
    """Resolve only current, formal relationship facts for renewal support.

    This reader never creates an appointment, relation, task, or notification.
    A Volunteer 2.0 service target is the source for service relationships;
    legacy appointment rows remain readable only through their formal
    ``org_unit_id`` compatibility field.
    """

    context = _current_member_context(member_id, actor_user_id)
    roles: dict[str, list[dict[str, Any]]] = {key: [] for key in ROLE_COLLECTIONS}
    referrers, referrer_resolution = _referrer_entry(context)
    roles["referrers"] = referrers

    groups = context["groups"]
    classes = context["classes"]
    center = context["center"]
    group_ids = [row["org_unit_id"] for row in groups]
    class_ids = [row["org_unit_id"] for row in classes]

    roles["group_leaders"] = _dedupe_role_entries(
        [
            _supporter_entry(row, role="GROUP_LEADER")
            for row in _appointment_rows(
                target_org_unit_ids=group_ids,
                position_keys=("volunteer_group_leader",),
            )
        ]
    )
    roles["group_counselors"] = _dedupe_role_entries(
        [
            _supporter_entry(row, role="GROUP_COUNSELOR")
            for row in _appointment_rows(
                target_org_unit_ids=group_ids,
                position_keys=("volunteer_group_counselor",),
            )
        ]
    )
    class_teacher_entries: list[dict[str, Any]] = []
    for row in _appointment_rows(
        target_org_unit_ids=class_ids,
        position_keys=("volunteer_class_counselor", "volunteer_deputy_class_teacher"),
    ):
        class_teacher_entries.append(
            _supporter_entry(
                row,
                role=(
                    "DEPUTY_CLASS_TEACHER"
                    if row["appointment_key"] == "volunteer_deputy_class_teacher"
                    else "CLASS_TEACHER"
                ),
            )
        )
    roles["class_teachers"] = _dedupe_role_entries(class_teacher_entries)
    roles["class_development"] = _dedupe_role_entries(
        [
            _supporter_entry(row, role="CLASS_DEVELOPMENT")
            for row in _appointment_rows(
                target_org_unit_ids=class_ids,
                system_type="COMMITTEE_LINE",
                line_type="DEVELOPMENT",
            )
        ]
    )
    roles["center_development"] = _dedupe_role_entries(
        [
            _supporter_entry(row, role="CENTER_DEVELOPMENT")
            for row in _appointment_rows(
                target_org_unit_ids=[center["id"]] if center else [],
                line_type="DEVELOPMENT",
            )
        ]
    )
    return {
        "recommended_supporters": _recommended_supporters(roles),
        "roles": roles,
        "data_quality": {
            "referrer_resolution": referrer_resolution,
            "current_center_available": bool(center),
            "current_class_available": bool(classes),
            "current_group_available": bool(groups),
            "eligible_supporter_count": sum(len(items) for items in roles.values()),
        },
    }


def _cycle_context(cycle_id: int, actor_user_id: int) -> dict[str, Any]:
    row = fetch_one(
        "SELECT c.id, c.member_id, c.status, m.org_unit_id AS member_org_unit_id "
        "FROM renewal_cycles c JOIN members m ON m.id=c.member_id WHERE c.id=?",
        (cycle_id,),
    )
    if not row:
        raise ValueError("续费周期不存在")
    cycle = dict(row)
    _member_scope(int(cycle["member_id"]), cycle["member_org_unit_id"], actor_user_id)
    return cycle


def _latest_needs_support(cycle_id: int) -> bool:
    row = fetch_one(
        "SELECT needs_support FROM renewal_followups WHERE renewal_cycle_id=? "
        "ORDER BY followed_at DESC, id DESC LIMIT 1",
        (cycle_id,),
    )
    return bool(row and row.get("needs_support"))


def _request_rows(cycle_id: int) -> list[dict[str, Any]]:
    rows = fetch_all(
        "SELECT r.id, r.renewal_cycle_id, r.supporter_member_id, r.supporter_person_id, "
        "r.supporter_name_snapshot, r.supporter_role, r.supporter_org_unit_id, "
        "support_org.name AS supporter_org_name, r.status, r.requested_by, "
        "requested_by.display_name AS requested_by_name, r.requested_at, "
        "r.feedback_summary, r.next_action, r.feedback_by, "
        "feedback_by.display_name AS feedback_by_name, r.feedback_at, r.closed_at "
        "FROM renewal_support_requests r "
        "LEFT JOIN org_units support_org ON support_org.id=r.supporter_org_unit_id "
        "LEFT JOIN app_users requested_by ON requested_by.id=r.requested_by "
        "LEFT JOIN app_users feedback_by ON feedback_by.id=r.feedback_by "
        "WHERE r.renewal_cycle_id=? ORDER BY r.requested_at DESC, r.id DESC",
        (cycle_id,),
    )
    return [dict(row) for row in rows]


def _request_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    status = str(row["status"] or "REQUESTED").upper()
    return {
        "id": int(row["id"]),
        "supporter_member_id": (
            int(row["supporter_member_id"])
            if row.get("supporter_member_id") is not None
            else None
        ),
        "supporter_person_id": row.get("supporter_person_id"),
        "supporter_name_snapshot": row["supporter_name_snapshot"],
        "supporter_role": row["supporter_role"],
        "supporter_role_label": _role_label(str(row["supporter_role"])),
        "supporter_org_unit_id": row.get("supporter_org_unit_id"),
        "supporter_org_name": row.get("supporter_org_name"),
        "status": status,
        "status_label": status_label(status),
        "requested_by": row.get("requested_by"),
        "requested_by_name": row.get("requested_by_name"),
        "requested_at": row.get("requested_at"),
        "feedback_summary": _redact_text(row.get("feedback_summary")),
        "next_action": _redact_text(row.get("next_action")),
        "feedback_by": row.get("feedback_by"),
        "feedback_by_name": row.get("feedback_by_name"),
        "feedback_at": row.get("feedback_at"),
        "closed_at": row.get("closed_at"),
    }


def status_label(code: str) -> str:
    return SUPPORT_STATUS_META.get(code, code)


def support_status(
    *,
    cycle_status: str,
    needs_support: bool,
    requests: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    if str(cycle_status or "").upper() in CLOSED_CYCLE_STATUSES or not needs_support:
        code = "NONE"
    else:
        open_statuses = {str(row.get("status") or "").upper() for row in requests}
        if "FEEDBACK_RECEIVED" in open_statuses:
            code = "FEEDBACK_RECEIVED"
        elif "REQUESTED" in open_statuses:
            code = "IN_PROGRESS"
        else:
            code = "PENDING"
    return {"code": code, "label": status_label(code)}


def list_cycle_support_statuses(
    cycles: Iterable[Mapping[str, Any]],
    *,
    latest_needs_support: Mapping[int, bool] | None = None,
) -> dict[int, dict[str, str]]:
    """Return lightweight support states for renewal list rows in two queries."""

    cycle_rows = [dict(row) for row in cycles]
    if not cycle_rows:
        return {}
    cycle_ids = sorted({int(row["id"]) for row in cycle_rows})
    placeholders = ",".join("?" for _ in cycle_ids)
    requests_by_cycle: dict[int, list[dict[str, Any]]] = {cycle_id: [] for cycle_id in cycle_ids}
    for row in fetch_all(
        "SELECT renewal_cycle_id, status FROM renewal_support_requests "
        "WHERE renewal_cycle_id IN (" + placeholders + ")",
        tuple(cycle_ids),
    ):
        requests_by_cycle[int(row["renewal_cycle_id"])].append(dict(row))
    needs = dict(latest_needs_support or {})
    missing = [cycle_id for cycle_id in cycle_ids if cycle_id not in needs]
    if missing:
        followup_placeholders = ",".join("?" for _ in missing)
        followups = fetch_all(
            "SELECT id, renewal_cycle_id, needs_support FROM renewal_followups "
            "WHERE renewal_cycle_id IN (" + followup_placeholders + ") "
            "ORDER BY followed_at DESC, id DESC",
            tuple(missing),
        )
        for row in followups:
            cycle_id = int(row["renewal_cycle_id"])
            if cycle_id not in needs:
                needs[cycle_id] = bool(row.get("needs_support"))
        for cycle_id in missing:
            needs.setdefault(cycle_id, False)
    return {
        int(row["id"]): support_status(
            cycle_status=str(row.get("status") or ""),
            needs_support=bool(needs.get(int(row["id"]), False)),
            requests=requests_by_cycle[int(row["id"])],
        )
        for row in cycle_rows
    }


def list_renewal_support_requests(cycle_id: int, actor_user_id: int) -> list[dict[str, Any]]:
    _cycle_context(cycle_id, actor_user_id)
    return [_request_payload(row) for row in _request_rows(cycle_id)]


def _network_supporter(
    network: Mapping[str, Any],
    *,
    supporter_role: str,
    supporter_member_id: int | None,
    supporter_person_id: str | None,
    supporter_name_snapshot: str,
) -> dict[str, Any]:
    role = supporter_role.strip().upper()
    if role not in SUPPORT_ROLE_META:
        raise ValueError("助力关系角色无效")
    entries = [
        entry
        for items in network["roles"].values()
        for entry in items
        if entry["role"] == role
    ]
    if supporter_member_id is not None:
        entries = [item for item in entries if item.get("member_id") == supporter_member_id]
    if supporter_person_id:
        entries = [item for item in entries if item.get("person_id") == supporter_person_id]
    if supporter_member_id is None and not supporter_person_id:
        entries = [
            item
            for item in entries
            if not item.get("resolved") and _text(item.get("name")) == supporter_name_snapshot
        ]
    entries = [
        item for item in entries if _text(item.get("name")) == supporter_name_snapshot
    ]
    if len(entries) != 1:
        raise ValueError("助力人不在当前可解析的关系网络中，请先刷新行动卡")
    return entries[0]


def _open_duplicate_request(
    connection,
    *,
    cycle_id: int,
    supporter: Mapping[str, Any],
) -> dict[str, Any] | None:
    conditions = [
        "renewal_cycle_id=?",
        "status IN ('REQUESTED','FEEDBACK_RECEIVED')",
    ]
    params: list[Any] = [cycle_id]
    if supporter.get("member_id") is not None:
        conditions.append("supporter_member_id=?")
        params.append(int(supporter["member_id"]))
    elif supporter.get("person_id"):
        conditions.append("supporter_person_id=?")
        params.append(supporter["person_id"])
    else:
        conditions.extend(
            [
                "supporter_member_id IS NULL",
                "supporter_person_id IS NULL",
                "supporter_role=?",
                "supporter_name_snapshot=?",
            ]
        )
        params.extend([supporter["role"], supporter["name"]])
    row = execute(
        connection,
        "SELECT id FROM renewal_support_requests WHERE " + " AND ".join(conditions) + " "
        "ORDER BY id DESC LIMIT 1",
        tuple(params),
    ).fetchone()
    return dict(row) if row else None


def create_renewal_support_request(
    cycle_id: int,
    actor_user_id: int,
    *,
    supporter_role: str,
    supporter_member_id: int | None = None,
    supporter_person_id: str | None = None,
    supporter_name_snapshot: str,
) -> dict[str, Any]:
    cycle = _cycle_context(cycle_id, actor_user_id)
    if str(cycle["status"] or "").upper() in CLOSED_CYCLE_STATUSES:
        raise ValueError("本续费周期已闭环，不能新建协同助力请求")
    if not _latest_needs_support(cycle_id):
        raise ValueError("最近一次续费沟通未标记需要协同助力")
    name = _text(supporter_name_snapshot)
    if not name or len(name) > 128:
        raise ValueError("助力人姓名长度应为 1 至 128 个字符")
    network = build_renewal_support_network(int(cycle["member_id"]), actor_user_id)
    supporter = _network_supporter(
        network,
        supporter_role=supporter_role,
        supporter_member_id=supporter_member_id,
        supporter_person_id=_text(supporter_person_id) or None,
        supporter_name_snapshot=name,
    )
    now = _now_text()
    with transaction() as connection:
        existing = _open_duplicate_request(
            connection,
            cycle_id=cycle_id,
            supporter=supporter,
        )
        if existing:
            row = execute(
                connection,
                "SELECT * FROM renewal_support_requests WHERE id=?",
                (existing["id"],),
            ).fetchone()
            payload = _request_payload(dict(row))
            payload["created"] = False
            return payload
        cursor = execute(
            connection,
            "INSERT INTO renewal_support_requests "
            "(renewal_cycle_id, supporter_member_id, supporter_person_id, supporter_name_snapshot, "
            "supporter_role, supporter_org_unit_id, status, requested_by, requested_at, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'REQUESTED', ?, ?, ?, ?)",
            (
                cycle_id,
                supporter.get("member_id"),
                supporter.get("person_id"),
                supporter["name"],
                supporter["role"],
                supporter.get("org_unit_id"),
                actor_user_id,
                now,
                now,
                now,
            ),
        )
        request_id = int(cursor.lastrowid)
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="renewals.support_request.create",
            resource_type="renewal_support_request",
            resource_id=str(request_id),
            org_unit_id=cycle["member_org_unit_id"],
            purpose="记录续费协同助力请求",
            after={
                "renewal_cycle_id": cycle_id,
                "supporter_member_id": supporter.get("member_id"),
                "supporter_person_id": supporter.get("person_id"),
                "supporter_role": supporter["role"],
                "status": "REQUESTED",
            },
        )
        row = execute(
            connection,
            "SELECT * FROM renewal_support_requests WHERE id=?",
            (request_id,),
        ).fetchone()
    payload = _request_payload(dict(row))
    payload["created"] = True
    return payload


def _optional_note(value: object, field: str) -> str | None | object:
    if value is _UNSET:
        return _UNSET
    if value is None:
        return None
    text = _text(value)
    if len(text) > 1000:
        raise ValueError(f"{field}不能超过1000个字符")
    return text or None


def update_renewal_support_request(
    cycle_id: int,
    request_id: int,
    actor_user_id: int,
    *,
    status: str | object = _UNSET,
    feedback_summary: str | None | object = _UNSET,
    next_action: str | None | object = _UNSET,
) -> dict[str, Any]:
    cycle = _cycle_context(cycle_id, actor_user_id)
    feedback = _optional_note(feedback_summary, "反馈")
    next_step = _optional_note(next_action, "后续行动")
    with transaction() as connection:
        row = execute(
            connection,
            "SELECT * FROM renewal_support_requests WHERE id=? AND renewal_cycle_id=?",
            (request_id, cycle_id),
        ).fetchone()
        if not row:
            raise ValueError("助力协调记录不存在")
        previous = dict(row)
        previous_status = str(previous["status"] or "").upper()
        desired_status = previous_status if status is _UNSET else _text(status).upper()
        if desired_status not in SUPPORT_REQUEST_STATUSES:
            raise ValueError("助力协调状态无效")
        if previous_status in {"CLOSED", "CANCELLED"} and desired_status != previous_status:
            raise ValueError("已关闭或已取消的协调记录不能重新打开")
        if desired_status == "REQUESTED" and previous_status != "REQUESTED":
            raise ValueError("已进入反馈或关闭状态的协调记录不能重置为待反馈")
        effective_feedback = previous.get("feedback_summary") if feedback is _UNSET else feedback
        if desired_status == "FEEDBACK_RECEIVED" and not _text(effective_feedback):
            raise ValueError("标记已有反馈时请填写简短反馈")
        effective_next_action = previous.get("next_action") if next_step is _UNSET else next_step
        now = _now_text()
        feedback_changed = feedback is not _UNSET or desired_status == "FEEDBACK_RECEIVED"
        feedback_by = actor_user_id if feedback_changed else previous.get("feedback_by")
        feedback_at = now if feedback_changed else previous.get("feedback_at")
        closed_at = (
            now
            if desired_status in {"CLOSED", "CANCELLED"}
            else previous.get("closed_at")
        )
        execute(
            connection,
            "UPDATE renewal_support_requests SET status=?, feedback_summary=?, next_action=?, "
            "feedback_by=?, feedback_at=?, closed_at=?, updated_at=? WHERE id=?",
            (
                desired_status,
                effective_feedback,
                effective_next_action,
                feedback_by,
                feedback_at,
                closed_at,
                now,
                request_id,
            ),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="renewals.support_request.update",
            resource_type="renewal_support_request",
            resource_id=str(request_id),
            org_unit_id=cycle["member_org_unit_id"],
            purpose="更新续费协同助力状态",
            before={"status": previous_status},
            after={
                "status": desired_status,
                "feedback_recorded": bool(_text(effective_feedback)),
                "next_action_recorded": bool(_text(effective_next_action)),
            },
        )
        updated = execute(
            connection,
            "SELECT * FROM renewal_support_requests WHERE id=?",
            (request_id,),
        ).fetchone()
    return _request_payload(dict(updated))
