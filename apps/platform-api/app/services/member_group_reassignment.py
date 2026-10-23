"""Same-class group reassignment with a stale-preview guard and atomic saves."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

from app.core.settings import get_settings
from app.db import atomic_transaction, connect, execute
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids, user_context
from app.services.members import resolve_member_scope, update_member
from app.services.volunteer_positions import _relation_boundary_is_current, get_current_member_study_orgs
from app.services.study_membership import CURRENT_CLASS_ID_SQL


def _require_manager(actor_user_id):
    if "members:manage" not in (user_context(actor_user_id) or {}).get("permissions", []):
        raise PermissionError("无权批量调整学员小组")


def _class_and_groups(connection, actor_user_id, class_org_unit_id):
    _require_manager(actor_user_id)
    allowed = accessible_org_ids(actor_user_id, "members:manage")
    row = execute(connection, "SELECT id, name, unit_type, is_active FROM org_units WHERE id=?", (class_org_unit_id,)).fetchone()
    if not row or row["unit_type"] not in {"CLASS", "SPECIAL_COHORT"} or not row["is_active"]:
        raise ValueError("班级不存在或已停用")
    if allowed is not None and class_org_unit_id not in allowed:
        raise PermissionError("班级不在学员管理授权范围内")
    groups = [dict(item) for item in execute(connection, "SELECT id, name FROM org_units WHERE parent_id=? AND unit_type='GROUP' AND is_active=1 ORDER BY name, id", (class_org_unit_id,)).fetchall()]
    return dict(row), [item for item in groups if allowed is None or item["id"] in allowed]


def _member_snapshot(connection, actor_user_id, member_id, class_org_unit_id):
    member = execute(connection, "SELECT id, member_code, name, status, org_unit_id, updated_at FROM members WHERE id=?", (member_id,)).fetchone()
    if not member:
        raise ValueError("学员不存在")
    resolve_member_scope(member_id, member["org_unit_id"], accessible_org_ids(actor_user_id, "members:manage"))
    if member["status"] != "ACTIVE":
        raise ValueError("批量调组仅支持在册学员")
    relations = [dict(item) for item in execute(connection,
        "SELECT r.id, r.org_unit_id, r.relation_type, r.valid_from, r.valid_until, r.is_primary, "
        "o.name, o.parent_id, o.unit_type, o.is_active FROM member_org_relations r "
        "JOIN org_units o ON o.id=r.org_unit_id WHERE r.member_id=? "
        "AND r.relation_type IN ('STUDY_CLASS','SPECIAL_COHORT','STUDY_GROUP') ORDER BY r.id", (member_id,)).fetchall()]
    now = datetime.now(UTC)
    current = [item for item in relations if item["is_active"] and
               _relation_boundary_is_current(item["valid_from"], before_or_equal=True, now=now) and
               _relation_boundary_is_current(item["valid_until"], before_or_equal=False, now=now)]
    classes = [item for item in current if item["relation_type"] in {"STUDY_CLASS", "SPECIAL_COHORT"}]
    groups = [item for item in current if item["relation_type"] == "STUDY_GROUP"]
    formal = get_current_member_study_orgs(connection, member_id)
    if not formal['class'] or formal['class']['org_unit_id'] != class_org_unit_id:
        raise ValueError("学员当前班级不一致或归属不明确，请刷新核对")
    # A manager may deliberately resolve group-only conflicts after reviewing
    # the explicit target. The formal class must still be unambiguous above.
    # Never infer a target from the conflicting rows or bypass the preview.
    previous_group = formal['group']['org_name'] if formal['group'] else (
        '待核对：' + '、'.join(item['name'] for item in groups) if groups else '未分组')
    return {**dict(member), "group_org_unit_id": formal['group']['org_unit_id'] if formal['group'] else None,
            "group_name": previous_group, "relations": relations,
            "needs_relation_cleanup": len(groups) > 1}


def group_reassignment_roster(*, actor_user_id: int, class_org_unit_id: str) -> dict:
    connection = connect()
    try:
        klass, groups = _class_and_groups(connection, actor_user_id, class_org_unit_id)
        rows = execute(connection, f"SELECT m.id FROM members m WHERE {CURRENT_CLASS_ID_SQL}=? "
                       "AND m.status='ACTIVE' ORDER BY m.id", (class_org_unit_id,)).fetchall()
        members = []
        for row in rows:
            try:
                snapshot = _member_snapshot(connection, actor_user_id, row["id"], class_org_unit_id)
                members.append({key: snapshot[key] for key in ("id", "member_code", "name", "group_org_unit_id", "group_name")})
            except PermissionError:
                continue
            except ValueError:
                members.append({"id": row["id"], "name": "归属待核对", "member_code": "", "group_org_unit_id": None, "group_name": "请先逐人核对", "unavailable": True})
        return {"class_name": klass["name"], "groups": groups, "members": members}
    finally:
        connection.close()


def _preview(connection, actor_user_id, class_org_unit_id, assignments):
    if not assignments or len(assignments) > 500:
        raise ValueError("每次请选择1到500名学员")
    ids = [item["member_id"] for item in assignments]
    if any(type(item) is not int or item <= 0 for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("学员编号无效或重复")
    klass, groups = _class_and_groups(connection, actor_user_id, class_org_unit_id)
    group_map = {item["id"]: item for item in groups}
    rows, snapshots = [], []
    for item in sorted(assignments, key=lambda item: item["member_id"]):
        target = group_map.get(item["group_org_unit_id"])
        if not target:
            raise ValueError("目标小组须为本班在授权范围内的有效小组")
        before = _member_snapshot(connection, actor_user_id, item["member_id"], class_org_unit_id)
        snapshots.append({"before": before, "target": target})
        rows.append({"member_id": before["id"], "name": before["name"], "member_code": before["member_code"],
                     "old_group_name": before["group_name"], "new_group_name": target["name"],
                     "needs_relation_cleanup": before["needs_relation_cleanup"],
                     "changed": before["group_org_unit_id"] != target["id"] or before["needs_relation_cleanup"]})
    fingerprint = hashlib.sha256(json.dumps({"class": klass, "snapshots": snapshots}, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return {"rows": rows, "changed_count": sum(item["changed"] for item in rows), "preview_fingerprint": fingerprint}


def preview_group_reassignment(*, actor_user_id: int, class_org_unit_id: str, assignments: list[dict]) -> dict:
    connection = connect()
    try:
        return _preview(connection, actor_user_id, class_org_unit_id, assignments)
    finally:
        connection.close()


def apply_group_reassignment(*, actor_user_id: int, class_org_unit_id: str, assignments: list[dict], preview_fingerprint: str, reason: str) -> dict:
    settings = get_settings()
    if settings.deployment_read_only or (settings.is_production and not settings.allow_production_mutations):
        raise PermissionError("当前环境不允许修改学员小组")
    if not reason.strip() or len(reason) > 1000:
        raise ValueError("请填写调组原因（最多1000字）")
    with atomic_transaction() as connection:
        # Serialize against other batch and individual member updates.
        if isinstance(connection, sqlite3.Connection):
            execute(connection, "BEGIN IMMEDIATE")
        else:
            ids = sorted({item["member_id"] for item in assignments})
            if ids:
                placeholders = ",".join("?" for _ in ids)
                execute(connection, f"SELECT id FROM members WHERE id IN ({placeholders}) ORDER BY id FOR UPDATE", tuple(ids)).fetchall()
                execute(connection, f"SELECT id FROM member_org_relations WHERE member_id IN ({placeholders}) ORDER BY id FOR UPDATE", tuple(ids)).fetchall()
            execute(connection, "SELECT id FROM org_units WHERE id=? OR parent_id=? ORDER BY id FOR UPDATE", (class_org_unit_id, class_org_unit_id)).fetchall()
        preview = _preview(connection, actor_user_id, class_org_unit_id, assignments)
        if preview["preview_fingerprint"] != preview_fingerprint:
            raise ValueError("学员或小组已变化，请重新预览后保存")
        changed_ids = {item["member_id"] for item in preview["rows"] if item["changed"]}
        for item in sorted(assignments, key=lambda item: item["member_id"]):
            if item["member_id"] in changed_ids:
                update_member(actor_user_id, item["member_id"], {"group_org_unit_id": item["group_org_unit_id"]})
        write_audit(connection, actor_user_id=actor_user_id, action="members.group_reassignment",
                    resource_type="class", resource_id=class_org_unit_id, org_unit_id=class_org_unit_id,
                    purpose=reason.strip(), after={"rows": preview["rows"], "changed_count": len(changed_ids)})
    return {"changed_count": len(changed_ids)}
