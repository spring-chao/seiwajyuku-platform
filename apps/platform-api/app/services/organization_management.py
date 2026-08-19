from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.db import execute, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids


LEARNING_UNIT_TYPES = {"CLASS", "GROUP"}


def _rows(connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in execute(connection, sql, params).fetchall()]


def _unit(connection, unit_id: str) -> dict[str, Any] | None:
    row = execute(
        connection,
        "SELECT id, unit_code, name, unit_type, parent_id, is_active, "
        "active_from, active_until FROM org_units WHERE id=?",
        (unit_id,),
    ).fetchone()
    return dict(row) if row else None


def _ensure_allowed(actor_user_id: int, *unit_ids: str) -> None:
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and any(unit_id not in allowed for unit_id in unit_ids):
        raise PermissionError("组织不在当前账号授权范围内")


def _reference_counts(connection, unit: dict[str, Any]) -> dict[str, int]:
    now = datetime.now(UTC).isoformat()
    active_member_relations = int(
        execute(
            connection,
            "SELECT COUNT(*) AS count FROM member_org_relations r "
            "JOIN members m ON m.id=r.member_id "
            "WHERE r.org_unit_id=? AND m.status='ACTIVE' "
            "AND (r.valid_from IS NULL OR r.valid_from<=?) "
            "AND (r.valid_until IS NULL OR r.valid_until>=?)",
            (unit["id"], now, now),
        ).fetchone()["count"]
    )
    active_children = int(
        execute(
            connection,
            "SELECT COUNT(*) AS count FROM org_units WHERE parent_id=? AND is_active=1",
            (unit["id"],),
        ).fetchone()["count"]
    )
    active_events = int(
        execute(
            connection,
            "SELECT COUNT(*) AS count FROM attendance_event_groups "
            "WHERE study_org_unit_id=? AND status='ACTIVE'",
            (unit["id"],),
        ).fetchone()["count"]
    )
    return {
        "active_member_relations": active_member_relations,
        "active_children": active_children,
        "active_events": active_events,
    }


def group_member_transfer_options(actor_user_id: int, unit_id: str) -> dict[str, Any]:
    """Return current group members and safe sibling targets before a one-person transfer."""
    _ensure_allowed(actor_user_id, unit_id)
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        source = _unit(connection, unit_id)
        if not source or source["unit_type"] != "GROUP" or not source["is_active"]:
            raise ValueError("仅允许迁移启用小组中的当前关联学员")
        parent = _unit(connection, source["parent_id"])
        if not parent or parent["unit_type"] != "CLASS" or not parent["is_active"]:
            raise ValueError("该小组所属班级不存在或已停用")
        _ensure_allowed(actor_user_id, parent["id"])
        members = _rows(
            connection,
            "SELECT DISTINCT m.id AS member_id, m.member_code, m.name, m.phone_masked "
            "FROM member_org_relations r JOIN members m ON m.id=r.member_id "
            "WHERE r.org_unit_id=? AND r.relation_type='STUDY_GROUP' AND m.status='ACTIVE' "
            "AND (r.valid_from IS NULL OR r.valid_from<=?) "
            "AND (r.valid_until IS NULL OR r.valid_until>=?) "
            "ORDER BY m.name, m.id",
            (unit_id, now, now),
        )
        targets = _rows(
            connection,
            "SELECT id, name FROM org_units WHERE parent_id=? AND unit_type='GROUP' "
            "AND is_active=1 AND id<>? ORDER BY name, id",
            (parent["id"], unit_id),
        )
        allowed = accessible_org_ids(actor_user_id)
        if allowed is not None:
            targets = [item for item in targets if item["id"] in allowed]
    return {
        "source_group": {"id": source["id"], "name": source["name"]},
        "class": {"id": parent["id"], "name": parent["name"]},
        "members": members,
        "target_groups": targets,
    }


def transfer_group_member_relation(
    actor_user_id: int,
    unit_id: str,
    *,
    member_id: int,
    target_group_org_unit_id: str,
    reason: str,
    confirmation: str,
) -> dict[str, Any]:
    """Move one active learner relation between sibling groups and retain history."""
    if len(reason.strip()) < 6:
        raise ValueError("迁移依据至少需要6个字符")
    _ensure_allowed(actor_user_id, unit_id, target_group_org_unit_id)
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        source = _unit(connection, unit_id)
        target = _unit(connection, target_group_org_unit_id)
        if not source or source["unit_type"] != "GROUP" or not source["is_active"]:
            raise ValueError("来源小组不存在或已停用")
        if not target or target["unit_type"] != "GROUP" or not target["is_active"]:
            raise ValueError("目标小组不存在或已停用")
        if source["parent_id"] != target["parent_id"]:
            raise ValueError("目标小组必须属于同一班级")
        relation = execute(
            connection,
            "SELECT r.id, m.name FROM member_org_relations r JOIN members m ON m.id=r.member_id "
            "WHERE r.member_id=? AND r.org_unit_id=? AND r.relation_type='STUDY_GROUP' "
            "AND m.status='ACTIVE' AND (r.valid_from IS NULL OR r.valid_from<=?) "
            "AND (r.valid_until IS NULL OR r.valid_until>=?) ORDER BY r.id DESC LIMIT 1",
            (member_id, unit_id, now, now),
        ).fetchone()
        if not relation:
            raise ValueError("该学长已不在来源小组的当前关联中，请刷新后重试")
        expected = f"确认将{relation['name']}从{source['name']}转至{target['name']}"
        if confirmation.strip() != expected:
            raise ValueError("确认文字不匹配，未迁移小组关系")
        conflicting = execute(
            connection,
            "SELECT r.id FROM member_org_relations r JOIN org_units g ON g.id=r.org_unit_id "
            "WHERE r.member_id=? AND r.relation_type='STUDY_GROUP' AND g.parent_id=? "
            "AND r.org_unit_id NOT IN (?, ?) AND (r.valid_from IS NULL OR r.valid_from<=?) "
            "AND (r.valid_until IS NULL OR r.valid_until>=?) LIMIT 1",
            (member_id, source["parent_id"], unit_id, target_group_org_unit_id, now, now),
        ).fetchone()
        if conflicting:
            raise ValueError("该学长在本班还有其他有效小组关系，请先完成关系核对")
        execute(
            connection,
            "UPDATE member_org_relations SET is_primary=0, valid_until=?, updated_at=? WHERE id=?",
            (now, now, relation["id"]),
        )
        execute(
            connection,
            "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, "
            "source_type, valid_from, created_at, updated_at) VALUES (?, ?, 'STUDY_GROUP', 1, "
            "'ORG_MANUAL_TRANSFER', ?, ?, ?)",
            (member_id, target["id"], now, now, now),
        )
        execute(
            connection,
            "UPDATE members SET group_name=?, updated_at=? WHERE id=?",
            (target["name"], now, member_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="org.learning_group.member.transfer",
            resource_type="member_org_relation",
            resource_id=f"{member_id}:{source['id']}:{target['id']}",
            org_unit_id=source["parent_id"],
            purpose=reason.strip(),
            before={"member_id": member_id, "group_id": source["id"], "group_name": source["name"]},
            after={"member_id": member_id, "group_id": target["id"], "group_name": target["name"]},
        )
    return {
        "member_id": member_id,
        "member_name": relation["name"],
        "source_group_id": source["id"],
        "target_group_id": target["id"],
    }


def _sync_members_after_class_move(
    connection, *, class_id: str, target_center_id: str, now: str
) -> int:
    """Keep current member hierarchy aligned with the organization master.

    The organization tree is the source of truth. Historical follow-up and
    renewal records intentionally remain snapshots; current member scope and
    denormalized display fields are synchronized in the same transaction as
    the class move.
    """
    impacted = _rows(
        connection,
        "SELECT DISTINCT r.member_id FROM member_org_relations r "
        "WHERE ((r.relation_type='STUDY_CLASS' AND r.org_unit_id=?) "
        "OR (r.relation_type='STUDY_GROUP' AND r.org_unit_id IN ("
        "SELECT id FROM org_units WHERE parent_id=? AND unit_type='GROUP'))) "
        "AND (r.valid_from IS NULL OR r.valid_from<=?) "
        "AND (r.valid_until IS NULL OR r.valid_until>=?)",
        (class_id, class_id, now, now),
    )
    class_row = _unit(connection, class_id)
    for item in impacted:
        member_id = item["member_id"]
        group = execute(
            connection,
            "SELECT g.name FROM member_org_relations r "
            "JOIN org_units g ON g.id=r.org_unit_id "
            "WHERE r.member_id=? AND r.relation_type='STUDY_GROUP' "
            "AND g.parent_id=? AND g.is_active=1 "
            "AND (r.valid_from IS NULL OR r.valid_from<=?) "
            "AND (r.valid_until IS NULL OR r.valid_until>=?) "
            "ORDER BY r.id DESC LIMIT 1",
            (member_id, class_id, now, now),
        ).fetchone()
        execute(
            connection,
            "UPDATE members SET org_unit_id=?, class_name=?, group_name=?, updated_at=? "
            "WHERE id=?",
            (
                target_center_id,
                class_row["name"],
                group["name"] if group else None,
                now,
                member_id,
            ),
        )
        target_relation = execute(
            connection,
            "SELECT id FROM member_org_relations WHERE member_id=? "
            "AND org_unit_id=? AND relation_type='PRIMARY_REGION' LIMIT 1",
            (member_id, target_center_id),
        ).fetchone()
        if target_relation:
            execute(
                connection,
                "DELETE FROM member_org_relations WHERE member_id=? "
                "AND relation_type='PRIMARY_REGION' AND id<>?",
                (member_id, target_relation["id"]),
            )
            execute(
                connection,
                "UPDATE member_org_relations SET is_primary=1, valid_until=NULL, "
                "updated_at=? WHERE id=?",
                (now, target_relation["id"]),
            )
        else:
            current_relation = execute(
                connection,
                "SELECT id FROM member_org_relations WHERE member_id=? "
                "AND relation_type='PRIMARY_REGION' ORDER BY id DESC LIMIT 1",
                (member_id,),
            ).fetchone()
            if current_relation:
                execute(
                    connection,
                    "UPDATE member_org_relations SET org_unit_id=?, is_primary=1, "
                    "valid_until=NULL, updated_at=? WHERE id=?",
                    (target_center_id, now, current_relation["id"]),
                )
            else:
                execute(
                    connection,
                    "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, "
                    "is_primary, source_type, created_at, updated_at) "
                    "VALUES (?, ?, 'PRIMARY_REGION', 1, 'ORG_MASTER_SYNC', ?, ?)",
                    (member_id, target_center_id, now, now),
                )
    return len(impacted)


def list_learning_org_units(actor_user_id: int) -> dict[str, Any]:
    allowed = accessible_org_ids(actor_user_id)
    with transaction() as connection:
        units = _rows(
            connection,
            "SELECT o.id, o.unit_code, o.name, o.unit_type, o.parent_id, "
            "o.is_active, o.active_from, o.active_until, o.created_at, "
            "p.name AS parent_name, "
            "p.unit_type AS parent_type FROM org_units o "
            "LEFT JOIN org_units p ON p.id=o.parent_id "
            "WHERE o.unit_type IN ('CLASS','GROUP') "
            "ORDER BY o.is_active DESC, p.name, o.name, o.id",
        )
        if allowed is not None:
            units = [row for row in units if row["id"] in allowed]
        for unit in units:
            unit["reference_counts"] = _reference_counts(connection, unit)
        centers = _rows(
            connection,
            "SELECT id, name FROM org_units "
            "WHERE unit_type='REGIONAL_CENTER' AND is_active=1 ORDER BY name, id",
        )
        active_classes = [
            row for row in units if row["unit_type"] == "CLASS" and row["is_active"]
        ]
        canonical_by_scope_and_name: dict[tuple[str | None, str], dict[str, Any]] = {}
        for row in active_classes:
            key = (row.get("parent_id"), row["name"])
            current = canonical_by_scope_and_name.get(key)
            if current is None or (
                row.get("created_at") or "",
                row["id"],
            ) < (
                current.get("created_at") or "",
                current["id"],
            ):
                canonical_by_scope_and_name[key] = row
        for row in active_classes:
            key = (row.get("parent_id"), row["name"])
            row["is_name_canonical"] = (
                canonical_by_scope_and_name[key]["id"] == row["id"]
            )
        # Selection controls (for example "新增小组") must never expose
        # technical duplicate class nodes. The full ``units`` list remains
        # available for organization review and cleanup.
        classes = sorted(
            canonical_by_scope_and_name.values(),
            key=lambda row: (row.get("parent_name") or "", row["name"], row["id"]),
        )
        if allowed is not None:
            centers = [row for row in centers if row["id"] in allowed]
    return {"units": units, "centers": centers, "classes": classes}


def create_learning_org_unit(
    actor_user_id: int,
    *,
    name: str,
    unit_type: str,
    parent_id: str,
    confirmation: str,
) -> dict[str, Any]:
    normalized_name = name.strip()
    normalized_type = unit_type.strip().upper()
    if not normalized_name or len(normalized_name) > 255:
        raise ValueError("组织名称不能为空且不能超过255个字符")
    if normalized_type not in LEARNING_UNIT_TYPES:
        raise ValueError("只允许新增班级或小组")
    label = "班级" if normalized_type == "CLASS" else "小组"
    if confirmation.strip() != f"确认新增{label}：{normalized_name}":
        raise ValueError("确认文字不匹配，未新增组织")
    _ensure_allowed(actor_user_id, parent_id)
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        parent = _unit(connection, parent_id)
        expected_parent_type = "REGIONAL_CENTER" if normalized_type == "CLASS" else "CLASS"
        if not parent or not parent["is_active"] or parent["unit_type"] != expected_parent_type:
            raise ValueError(
                "班级必须建立在启用的分中心下" if normalized_type == "CLASS"
                else "小组必须建立在启用的普通班级下"
            )
        if normalized_type == "CLASS":
            duplicate = execute(
                connection,
                "SELECT id FROM org_units WHERE is_active=1 "
                "AND unit_type IN ('CLASS','SPECIAL_COHORT') AND name=?",
                (normalized_name,),
            ).fetchone()
        else:
            duplicate = execute(
                connection,
                "SELECT id FROM org_units WHERE is_active=1 AND unit_type='GROUP' "
                "AND parent_id=? AND name=?",
                (parent_id, normalized_name),
            ).fetchone()
        if duplicate:
            raise ValueError(f"已存在启用的同名{label}")
        prefix = "class" if normalized_type == "CLASS" else "group"
        unit_id = f"org-{prefix}-{uuid4().hex[:16]}"
        unit_code = f"{normalized_type}_{uuid4().hex[:20].upper()}"
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
            "is_active, active_from, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (unit_id, unit_code, normalized_name, normalized_type, parent_id, now[:10], now, now),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="org.learning_unit.create",
            resource_type="org_unit",
            resource_id=unit_id,
            org_unit_id=parent_id,
            purpose=f"新增{label}",
            after={
                "name": normalized_name,
                "unit_type": normalized_type,
                "parent_id": parent_id,
            },
        )
    return {"id": unit_id, "name": normalized_name, "unit_type": normalized_type}


def preview_learning_org_move(
    actor_user_id: int, unit_id: str, *, target_parent_id: str
) -> dict[str, Any]:
    _ensure_allowed(actor_user_id, target_parent_id)
    with transaction() as connection:
        unit = _unit(connection, unit_id)
        target = _unit(connection, target_parent_id)
        if not unit or unit["unit_type"] != "CLASS" or not unit["is_active"]:
            raise ValueError("仅允许调整启用普通班级的归属")
        if not target or target["unit_type"] != "REGIONAL_CENTER" or not target["is_active"]:
            raise ValueError("目标必须是启用的区域分中心")
        allowed = accessible_org_ids(actor_user_id)
        if allowed is not None and unit_id not in allowed:
            raise PermissionError("原班级不在当前账号授权范围内")
        counts = _reference_counts(connection, unit)
        return {
            "unit_id": unit["id"],
            "class_name": unit["name"],
            "current_parent_id": unit["parent_id"],
            "target_parent_id": target["id"],
            "target_parent_name": target["name"],
            "reference_counts": counts,
            "confirmation": f"确认调整{unit['name']}归属为{target['name']}",
        }


def move_learning_org_unit(
    actor_user_id: int,
    unit_id: str,
    *,
    target_parent_id: str,
    reason: str,
    confirmation: str,
) -> dict[str, Any]:
    if len(reason.strip()) < 6:
        raise ValueError("调整原因至少需要6个字符")
    preview = preview_learning_org_move(
        actor_user_id, unit_id, target_parent_id=target_parent_id
    )
    if confirmation.strip() != preview["confirmation"]:
        raise ValueError("确认文字不匹配，未调整班级归属")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        unit = _unit(connection, unit_id)
        before_parent_id = unit["parent_id"]
        execute(
            connection,
            "UPDATE org_units SET parent_id=?, updated_at=? WHERE id=?",
            (target_parent_id, now, unit_id),
        )
        synced_member_count = _sync_members_after_class_move(
            connection,
            class_id=unit_id,
            target_center_id=target_parent_id,
            now=now,
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="org.learning_class.move",
            resource_type="org_unit",
            resource_id=unit_id,
            org_unit_id=target_parent_id,
            purpose=reason.strip(),
            before={"parent_id": before_parent_id},
            after={
                "parent_id": target_parent_id,
                "synced_member_count": synced_member_count,
                "source_of_truth": "org_units",
            },
        )
    return {
        **preview,
        "previous_parent_id": before_parent_id,
        "synced_member_count": synced_member_count,
    }


def deactivate_learning_org_unit(
    actor_user_id: int,
    unit_id: str,
    *,
    reason: str,
    confirmation: str,
) -> dict[str, Any]:
    if len(reason.strip()) < 6:
        raise ValueError("停用原因至少需要6个字符")
    _ensure_allowed(actor_user_id, unit_id)
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        unit = _unit(connection, unit_id)
        if not unit or unit["unit_type"] not in LEARNING_UNIT_TYPES or not unit["is_active"]:
            raise ValueError("仅允许停用当前启用的班级或小组")
        label = "班级" if unit["unit_type"] == "CLASS" else "小组"
        expected = f"确认停用{label}：{unit['name']}"
        if confirmation.strip() != expected:
            raise ValueError("确认文字不匹配，未停用组织")
        counts = _reference_counts(connection, unit)
        if any(counts.values()):
            raise ValueError(
                "该组织仍有关联的在册学员、启用子组织或活动，需先完成转移后再停用"
            )
        execute(
            connection,
            "UPDATE org_units SET is_active=0, active_until=?, updated_at=? WHERE id=?",
            (now[:10], now, unit_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="org.learning_unit.deactivate",
            resource_type="org_unit",
            resource_id=unit_id,
            org_unit_id=unit["parent_id"],
            purpose=reason.strip(),
            before={"is_active": True},
            after={"is_active": False, "active_until": now[:10]},
        )
    return {"id": unit_id, "name": unit["name"], "is_active": False}
