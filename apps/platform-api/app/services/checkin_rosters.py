"""member_org_relations service - multi-relation model for class/group/special cohort.

This module manages the many-to-many relationship between members and org units,
enabling班主任 to see only their class, 组长 to see only their group,
and黄埔班 managers to see only special cohort members.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids


RELATION_TYPES = {
    "PRIMARY_REGION": "主分中心",
    "STUDY_CLASS": "班级",
    "STUDY_GROUP": "小组",
    "SPECIAL_COHORT": "特殊班(黄埔班等)",
    "DEVELOPMENT_RELATION": "发展关系",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _all_classes() -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT id, unit_code, name, parent_id FROM org_units "
        "WHERE unit_type='CLASS' AND is_active=1 ORDER BY name"
    )


def _all_groups(class_org_unit_id: str | None = None) -> list[dict[str, Any]]:
    params: list[Any] = []
    sql = (
        "SELECT id, unit_code, name, parent_id FROM org_units "
        "WHERE unit_type='GROUP' AND is_active=1"
    )
    if class_org_unit_id:
        sql += " AND parent_id=?"
        params.append(class_org_unit_id)
    sql += " ORDER BY name"
    return fetch_all(sql, tuple(params))


def _all_special_cohorts() -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT id, unit_code, name, parent_id FROM org_units "
        "WHERE unit_type='SPECIAL_COHORT' AND is_active=1 ORDER BY name"
    )


def list_classes(user_id: int) -> list[dict[str, Any]]:
    """List all class org units visible to the user."""
    if user_id == 0:
        return _all_classes()
    allowed = accessible_org_ids(user_id)
    if allowed is None:
        return _all_classes()
    if not allowed:
        return []
    placeholders = ",".join("?" for _ in allowed)
    return fetch_all(
        f"SELECT id, unit_code, name, parent_id FROM org_units "
        f"WHERE unit_type='CLASS' AND is_active=1 AND id IN ({placeholders}) ORDER BY name",
        tuple(sorted(allowed)),
    )


def list_groups(user_id: int, class_org_unit_id: str | None = None) -> list[dict[str, Any]]:
    """List all group org units visible to the user."""
    if user_id == 0:
        return _all_groups(class_org_unit_id)
    allowed = accessible_org_ids(user_id)
    if allowed is None:
        return _all_groups(class_org_unit_id)
    if not allowed:
        return []
    params = list(sorted(allowed))
    placeholders = ",".join("?" for _ in allowed)
    sql = (
        f"SELECT id, unit_code, name, parent_id FROM org_units "
        f"WHERE unit_type='GROUP' AND is_active=1 AND id IN ({placeholders})"
    )
    if class_org_unit_id:
        sql += " AND parent_id=?"
        params.append(class_org_unit_id)
    sql += " ORDER BY name"
    return fetch_all(sql, tuple(params))


def list_special_cohorts(user_id: int) -> list[dict[str, Any]]:
    """List all special cohort org units (e.g. 黄埔班)."""
    if user_id == 0:
        return _all_special_cohorts()
    allowed = accessible_org_ids(user_id)
    if allowed is None:
        return _all_special_cohorts()
    if not allowed:
        return []
    placeholders = ",".join("?" for _ in allowed)
    return fetch_all(
        f"SELECT id, unit_code, name, parent_id FROM org_units "
        f"WHERE unit_type='SPECIAL_COHORT' AND is_active=1 AND id IN ({placeholders}) ORDER BY name",
        tuple(sorted(allowed)),
    )


def roster_options(user_id: int) -> dict[str, Any]:
    """Return class/group/special_cohort options for roster selection."""
    return {
        "classes": list_classes(user_id),
        "groups": list_groups(user_id),
        "special_cohorts": list_special_cohorts(user_id),
    }


def roster_members(
    user_id: int,
    *,
    org_unit_id: str | None = None,
    class_org_unit_id: str | None = None,
    group_org_unit_id: str | None = None,
    special_cohort_org_unit_id: str | None = None,
    include_phone: bool = False,
) -> list[dict[str, Any]]:
    """Return roster members for a given class/group/special_cohort.

    Uses member_org_relations for accurate scoping.
    When include_phone=True (machine-to-machine via API key), returns decrypted
    phone for checkin matching. Otherwise returns phone_masked only.
    """
    is_system = user_id == 0
    if not is_system:
        allowed = accessible_org_ids(user_id)
    else:
        allowed = None

    if group_org_unit_id and class_org_unit_id:
        group = fetch_one(
            "SELECT parent_id FROM org_units WHERE id=? AND unit_type='GROUP'",
            (group_org_unit_id,),
        )
        if not group or group["parent_id"] != class_org_unit_id:
            raise ValueError("小组不属于指定班级")
    selectors = [
        bool(org_unit_id),
        bool(group_org_unit_id),
        bool(special_cohort_org_unit_id),
        bool(class_org_unit_id and not group_org_unit_id),
    ]
    if sum(selectors) != 1:
        raise ValueError("每次只能指定一个分中心、班级、小组或特殊班范围")

    relations: list[tuple[str, str]] = []
    if group_org_unit_id:
        relations.append(("STUDY_GROUP", group_org_unit_id))
    elif class_org_unit_id:
        relations.append(("STUDY_CLASS", class_org_unit_id))
    elif special_cohort_org_unit_id:
        relations.append(("SPECIAL_COHORT", special_cohort_org_unit_id))
    elif org_unit_id:
        relations.append(("PRIMARY_REGION", org_unit_id))

    if allowed is not None:
        for _, org_id in relations:
            if org_id not in allowed:
                raise PermissionError(f"没有组织 {org_id} 的授权")

    now = _now()
    results: list[dict[str, Any]] = []
    seen_member_ids: set[int] = set()

    phone_select = "m.phone_ciphertext, " if include_phone else ""
    for relation_type, org_id in relations:
        rows = fetch_all(
            f"SELECT mor.member_id, m.member_code, m.name, m.phone_masked, "
            f"{phone_select}"
            "m.org_unit_id AS primary_org_id, o.name AS primary_org_name, "
            "m.company_name, mor.org_unit_id AS relation_org_id, "
            "mor.relation_type, mor.is_primary "
            "FROM member_org_relations mor "
            "JOIN members m ON m.id=mor.member_id "
            "JOIN org_units o ON o.id=m.org_unit_id "
            "WHERE mor.org_unit_id=? AND mor.relation_type=? "
            "AND (mor.valid_from IS NULL OR mor.valid_from<=?) "
            "AND (mor.valid_until IS NULL OR mor.valid_until>=?) "
            "AND m.status='ACTIVE' "
            "ORDER BY m.name",
            (org_id, relation_type, now, now),
        )
        for row in rows:
            if row["member_id"] not in seen_member_ids:
                seen_member_ids.add(row["member_id"])
                item = dict(row)
                if include_phone and item.get("phone_ciphertext"):
                    from app.core.privacy import decrypt_text
                    item["phone"] = decrypt_text(item.pop("phone_ciphertext"))
                elif "phone_ciphertext" in item:
                    item.pop("phone_ciphertext")
                results.append(item)

    return results


def upsert_relation(
    actor_user_id: int,
    *,
    member_id: int,
    org_unit_id: str,
    relation_type: str,
    is_primary: bool = False,
    valid_from: str | None = None,
    valid_until: str | None = None,
    source_type: str = "MANUAL",
) -> int:
    """Create or update a member-org relation."""
    if relation_type not in RELATION_TYPES:
        raise ValueError(f"无效的关系类型: {relation_type}")

    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and org_unit_id not in allowed:
        raise PermissionError("不能在授权组织之外创建关系")

    now = _now()
    with transaction() as connection:
        existing = execute(
            connection,
            "SELECT id FROM member_org_relations "
            "WHERE member_id=? AND org_unit_id=? AND relation_type=?",
            (member_id, org_unit_id, relation_type),
        ).fetchone()
        if existing:
            rel_id = existing["id"] if hasattr(existing, "keys") else existing[0]
            execute(
                connection,
                "UPDATE member_org_relations SET is_primary=?, valid_from=?, valid_until=?, "
                "source_type=?, updated_at=? WHERE id=?",
                (1 if is_primary else 0, valid_from, valid_until, source_type, now, rel_id),
            )
        else:
            cursor = execute(
                connection,
                "INSERT INTO member_org_relations"
                "(member_id, org_unit_id, relation_type, is_primary, valid_from, valid_until, "
                "source_type, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    member_id, org_unit_id, relation_type,
                    1 if is_primary else 0, valid_from, valid_until,
                    source_type, now, now,
                ),
            )
            rel_id = cursor.lastrowid
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="members.org_relation.upsert",
            resource_type="member_org_relation",
            resource_id=str(rel_id),
            after={
                "member_id": member_id,
                "org_unit_id": org_unit_id,
                "relation_type": relation_type,
                "is_primary": is_primary,
            },
        )
        return rel_id


def member_relations(member_id: int, actor_user_id: int) -> list[dict[str, Any]]:
    """List all org relations for a member."""
    allowed = accessible_org_ids(actor_user_id)
    member = fetch_one("SELECT org_unit_id FROM members WHERE id=?", (member_id,))
    if not member:
        raise ValueError("学长不存在")
    if allowed is not None and member["org_unit_id"] not in allowed:
        raise PermissionError("学长不在组织授权范围内")
    return fetch_all(
        "SELECT mor.*, o.name AS org_name, o.unit_type "
        "FROM member_org_relations mor "
        "JOIN org_units o ON o.id=mor.org_unit_id "
        "WHERE mor.member_id=? "
        "ORDER BY mor.is_primary DESC, mor.relation_type",
        (member_id,),
    )
