"""Guarded phase-one import for ordinary class and group organization nodes."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping
from uuid import uuid4

from app.db import execute, fetch_one, transaction
from app.services.audit import write_audit
from app.services.class_roster_preflight import (
    _valid_group,
    preview_production_workbook,
    read_workbook,
)
from app.services.direct_class_preflight import (
    DEVELOPMENT_CENTERS,
    DIRECT_CLASSES,
)


CONFIRMED_SOURCE_SHA256 = (
    "59b621084faee13c02332e5735583aecaa7fa54f967a73e86164ab9eeca404cf"
)
CONFIRMATION_TEXT = "确认创建20个普通班和112个普通班小组"
IMPORT_TYPE = "CLASS_ROSTER_ORG_PHASE1_20260730"
RELATION_IMPORT_TYPE = "CLASS_ROSTER_RELATIONS_PHASE2_20260730"
RELATION_SOURCE_TYPE = "CLASS_ROSTER_REL_P2"
EXPECTED_MATCHING = {
    "MANUAL_REVIEW": 28,
    "NO_PRODUCTION_MATCH": 84,
    "UNIQUE_ACTIVE_MATCH": 722,
}
EXPECTED_ISSUES = {
    "DUPLICATE_SOURCE_PHONE": 8,
    "INVALID_PHONE": 9,
    "MISSING_CLASS": 18,
    "MISSING_PHONE": 11,
}


def apply_confirmed_member_relations(
    content: bytes,
    source_name: str,
    actor_user_id: int,
) -> dict[str, Any]:
    """Add only missing study relations for uniquely matched, classed members."""
    source_sha256 = hashlib.sha256(content).hexdigest()
    if source_sha256 != CONFIRMED_SOURCE_SHA256:
        raise ValueError("工作簿指纹与已确认版本不一致，已停止写入")
    preview = preview_production_workbook(content, source_name, "2026 新在册表")
    expected_source = {
        "active_member_count": 834, "with_class_count": 816,
        "missing_class_count": 18, "ordinary_class_member_count": 693,
        "direct_class_member_count": 123, "ordinary_class_count": 20,
        "direct_class_count": 4, "ordinary_group_pair_count": 112,
        "direct_group_pair_count": 11,
    }
    if any(preview["source"].get(key) != value for key, value in expected_source.items()):
        raise ValueError("工作簿汇总与生产确认包不一致，已停止写入")
    if _summary(preview["matching"]["summary"], "status") != EXPECTED_MATCHING:
        raise ValueError("生产人员匹配汇总已变化，已停止写入")
    if _summary(preview["issues"], "code") != EXPECTED_ISSUES:
        raise ValueError("人工复核问题汇总已变化，已停止写入")
    if _summary(preview["organization"]["class_action_summary"], "action") != {"REUSE": 24}:
        raise ValueError("班级组织尚未全部就绪，已停止写入")
    if _summary(preview["organization"]["group_action_summary"], "action") != {"REUSE": 123}:
        raise ValueError("小组组织尚未全部就绪，已停止写入")
    rows, _ = read_workbook(content, "2026 新在册表")
    phone_counts = defaultdict(int)
    for row in rows:
        if row["phone_hash"]:
            phone_counts[row["phone_hash"]] += 1
    eligible = [
        row for row in rows
        if row["class_name"] and row["phone_hash"]
        and phone_counts[row["phone_hash"]] == 1
    ]
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        existing_batch = execute(connection, "SELECT id, status FROM import_batches WHERE import_type=? AND source_sha256=? ORDER BY id DESC LIMIT 1", (RELATION_IMPORT_TYPE, source_sha256)).fetchone()
        if existing_batch and existing_batch["status"] == "APPLIED":
            return {"batch_id": existing_batch["id"], "status": "ALREADY_APPLIED"}
        units = execute(connection, "SELECT id, name, unit_type, parent_id, is_active FROM org_units WHERE is_active=1").fetchall()
        members = execute(connection, "SELECT id, phone_hash, status FROM members WHERE phone_hash IN (" + ",".join("?" for _ in {r['phone_hash'] for r in eligible}) + ")", tuple(sorted({r['phone_hash'] for r in eligible}))).fetchall()
        by_phone: dict[str, list[Any]] = defaultdict(list)
        for member in members:
            by_phone[member["phone_hash"]].append(member)
        root = next((u for u in units if u.get("name") == "苏州塾" and u["unit_type"] == "ROOT"), None)
        if not root:
            raise ValueError("苏州塾根节点不唯一，事务已回滚")
        centers = {u["name"]: u["id"] for u in units if u["unit_type"] == "REGIONAL_CENTER"}
        target_classes: dict[tuple[str, str], str] = {}
        for row in eligible:
            parent_id = root["id"] if row["class_name"] in DIRECT_CLASSES else centers.get(row["center_name"], "")
            candidates = [u for u in units if u["unit_type"] == "CLASS" and u["name"] == row["class_name"] and u["parent_id"] == parent_id]
            if len(candidates) != 1:
                raise ValueError("班级组织状态在预检后变化，事务已回滚")
            target_classes[(row["center_name"], row["class_name"])] = candidates[0]["id"]
        relation_rows = execute(connection, "SELECT member_id, org_unit_id, relation_type FROM member_org_relations WHERE relation_type IN ('STUDY_CLASS','STUDY_GROUP')").fetchall()
        relation_set = {(r["member_id"], r["org_unit_id"], r["relation_type"]) for r in relation_rows}
        inserts: list[tuple[int, str, str]] = []
        matched = 0
        for row in eligible:
            candidates = [m for m in by_phone[row["phone_hash"]] if m["status"] == "ACTIVE"]
            if len(candidates) != 1:
                continue
            matched += 1
            member_id = candidates[0]["id"]
            class_id = target_classes[(row["center_name"], row["class_name"])]
            if (member_id, class_id, "STUDY_CLASS") not in relation_set:
                inserts.append((member_id, class_id, "STUDY_CLASS"))
            if _valid_group(row["class_name"], row["group_name"]):
                groups = [u for u in units if u["unit_type"] == "GROUP" and u["name"] == row["group_name"] and u["parent_id"] == class_id]
                if len(groups) != 1:
                    raise ValueError("小组组织状态在预检后变化，事务已回滚")
                if (member_id, groups[0]["id"], "STUDY_GROUP") not in relation_set:
                    inserts.append((member_id, groups[0]["id"], "STUDY_GROUP"))
        if not matched:
            raise ValueError("没有可写入的唯一匹配学员，事务已回滚")
        for member_id, org_unit_id, relation_type in inserts:
            execute(connection, "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, source_type, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?)", (member_id, org_unit_id, relation_type, RELATION_SOURCE_TYPE, now, now))
        cursor = execute(connection, "INSERT INTO import_batches(import_type, source_name, source_sha256, status, preview_json, created_by, created_at, applied_at) VALUES (?, ?, ?, 'APPLIED', ?, ?, ?, ?)", (RELATION_IMPORT_TYPE, source_name, source_sha256, json.dumps({"matched_members": matched, "relations_added": len(inserts), "members_changed": 0}, ensure_ascii=False), actor_user_id, now, now))
        batch_id = cursor.lastrowid
        write_audit(connection, actor_user_id=actor_user_id, action="class_roster_relations_phase2.apply", resource_type="import_batch", resource_id=str(batch_id), after={"matched_members": matched, "relations_added": len(inserts), "members_changed": 0})
    return {"batch_id": batch_id, "status": "APPLIED", "matched_members": matched, "relations_added": len(inserts), "members_changed": 0}


def _summary(
    items: Iterable[Mapping[str, Any]], key: str
) -> dict[str, int]:
    return {str(item[key]): int(item["count"]) for item in items}


def _ordinary_class_conflicts(
    units: Iterable[Mapping[str, Any]],
    class_name: str,
    expected_parent_id: str,
) -> list[Mapping[str, Any]]:
    """Existing importer compatibility check, scoped to the intended parent."""
    return [
        unit
        for unit in units
        if unit["unit_type"] == "CLASS"
        and unit["name"] == class_name
        and unit["is_active"]
        and unit["parent_id"] == expected_parent_id
    ]


def organization_topology(
    rows: Iterable[Mapping[str, str]],
) -> tuple[dict[str, str], set[tuple[str, str]]]:
    """Return ordinary class parents and parent-scoped ordinary groups."""
    class_centers: dict[str, set[str]] = defaultdict(set)
    groups: set[tuple[str, str]] = set()
    for row in rows:
        class_name = str(row.get("class_name") or "").strip()
        center_name = str(row.get("center_name") or "").strip()
        group_name = str(row.get("group_name") or "").strip()
        if class_name and class_name not in DIRECT_CLASSES and center_name:
            class_centers[class_name].add(center_name)
        if (
            class_name not in DIRECT_CLASSES
            and _valid_group(class_name, group_name)
        ):
            groups.add((class_name, group_name))
    if any(len(centers) != 1 for centers in class_centers.values()):
        raise ValueError("普通班存在多个发展分中心候选，已停止写入")
    classes = {
        class_name: next(iter(centers))
        for class_name, centers in class_centers.items()
    }
    if len(classes) != 20 or len(groups) != 112:
        raise ValueError("普通班或小组数量与确认包不一致，已停止写入")
    if set(classes.values()) - set(DEVELOPMENT_CENTERS):
        raise ValueError("普通班的发展分中心无效，已停止写入")
    return classes, groups


def _validate_preview(preview: Mapping[str, Any]) -> None:
    source = preview["source"]
    expected_source = {
        "active_member_count": 834,
        "with_class_count": 816,
        "missing_class_count": 18,
        "ordinary_class_member_count": 693,
        "direct_class_member_count": 123,
        "ordinary_class_count": 20,
        "direct_class_count": 4,
        "ordinary_group_pair_count": 112,
        "direct_group_pair_count": 11,
    }
    if any(source.get(key) != value for key, value in expected_source.items()):
        raise ValueError("工作簿汇总与生产确认包不一致，已停止写入")
    if _summary(preview["matching"]["summary"], "status") != EXPECTED_MATCHING:
        raise ValueError("生产人员匹配汇总已变化，已停止写入")
    if _summary(preview["issues"], "code") != EXPECTED_ISSUES:
        raise ValueError("人工复核问题汇总已变化，已停止写入")
    class_actions = _summary(
        preview["organization"]["class_action_summary"], "action"
    )
    group_actions = _summary(
        preview["organization"]["group_action_summary"], "action"
    )
    if class_actions != {"CREATE_OR_RESOLVE": 20, "REUSE": 4}:
        raise ValueError("班级组织状态已变化，已停止写入")
    if group_actions != {"REUSE": 11, "REVIEW": 112}:
        raise ValueError("小组组织状态已变化，已停止写入")
    if preview["organization"]["root_match_count"] != 1:
        raise ValueError("苏州塾根节点不唯一，已停止写入")
    if any(
        item["match_count"] != 1
        for item in preview["organization"][
            "development_center_match_counts"
        ]
    ):
        raise ValueError("六个发展分中心解析失败，已停止写入")


def apply_confirmed_org_import(
    content: bytes,
    source_name: str,
    confirmation_text: str,
    actor_user_id: int,
) -> dict[str, Any]:
    """Create only ordinary class/group nodes; never mutate member records."""
    if confirmation_text.strip() != CONFIRMATION_TEXT:
        raise ValueError("确认文字不匹配，已停止写入")
    source_sha256 = hashlib.sha256(content).hexdigest()
    if source_sha256 != CONFIRMED_SOURCE_SHA256:
        raise ValueError("工作簿指纹与已确认版本不一致，已停止写入")
    existing_batch = fetch_one(
        "SELECT id, status FROM import_batches "
        "WHERE import_type=? AND source_sha256=? "
        "ORDER BY id DESC LIMIT 1",
        (IMPORT_TYPE, source_sha256),
    )
    if existing_batch and existing_batch["status"] == "APPLIED":
        return {
            "batch_id": existing_batch["id"],
            "status": "ALREADY_APPLIED",
            "created_classes": 20,
            "created_groups": 112,
            "members_changed": 0,
        }

    preview = preview_production_workbook(
        content, source_name, "2026 新在册表"
    )
    _validate_preview(preview)
    rows, _ = read_workbook(content, "2026 新在册表")
    classes, groups = organization_topology(rows)
    sorted_classes = sorted(classes)
    class_order = {
        class_name: index
        for index, class_name in enumerate(sorted_classes, 1)
    }
    now = datetime.now(UTC).isoformat()

    with transaction() as connection:
        units = execute(
            connection,
            "SELECT id, unit_code, name, unit_type, parent_id, is_active "
            "FROM org_units",
        ).fetchall()
        roots = [
            unit
            for unit in units
            if unit["unit_code"] == "SZ_ROOT" and unit["is_active"]
        ]
        if len(roots) != 1:
            raise ValueError("苏州塾根节点不唯一，事务已回滚")
        center_candidates = {
            center_name: [
                unit
                for unit in units
                if unit["unit_type"] == "REGIONAL_CENTER"
                and unit["is_active"]
                and unit["name"] == center_name
            ]
            for center_name in DEVELOPMENT_CENTERS
        }
        if any(
            len(candidates) != 1
            for candidates in center_candidates.values()
        ):
            raise ValueError("六个发展分中心解析失败，事务已回滚")
        centers = {
            center_name: candidates[0]["id"]
            for center_name, candidates in center_candidates.items()
        }

        created_nodes: list[dict[str, str]] = []
        class_ids: dict[str, str] = {}
        for class_name in sorted_classes:
            class_index = class_order[class_name]
            center_name = classes[class_name]
            conflicts = _ordinary_class_conflicts(
                units, class_name, centers[center_name]
            )
            if conflicts:
                raise ValueError("普通班组织状态在预检后变化，事务已回滚")
            same_name = [
                unit for unit in units
                if unit["unit_type"] in {"CLASS", "SPECIAL_COHORT"}
                and unit["name"] == class_name and unit["is_active"]
            ]
            if same_name:
                raise ValueError("班级名称已存在，已停止创建重复组织")
            class_id = str(uuid4())
            unit_code = f"SZ_CLASS_20260730_{class_index:02d}"
            execute(
                connection,
                "INSERT INTO org_units"
                "(id, unit_code, name, unit_type, parent_id, is_active, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, 'CLASS', ?, 1, ?, ?)",
                (
                    class_id,
                    unit_code,
                    class_name,
                    centers[center_name],
                    now,
                    now,
                ),
            )
            class_ids[class_name] = class_id
            created_nodes.append(
                {
                    "id": class_id,
                    "unit_code": unit_code,
                    "unit_type": "CLASS",
                    "name": class_name,
                    "parent_id": centers[center_name],
                }
            )

        group_number_by_class: dict[str, int] = defaultdict(int)
        for class_name, group_name in sorted(groups):
            group_number_by_class[class_name] += 1
            class_index = class_order[class_name]
            group_index = group_number_by_class[class_name]
            group_id = str(uuid4())
            unit_code = (
                f"SZ_GROUP_20260730_{class_index:02d}_{group_index:02d}"
            )
            execute(
                connection,
                "INSERT INTO org_units"
                "(id, unit_code, name, unit_type, parent_id, is_active, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, 'GROUP', ?, 1, ?, ?)",
                (
                    group_id,
                    unit_code,
                    group_name,
                    class_ids[class_name],
                    now,
                    now,
                ),
            )
            created_nodes.append(
                {
                    "id": group_id,
                    "unit_code": unit_code,
                    "unit_type": "GROUP",
                    "name": group_name,
                    "parent_id": class_ids[class_name],
                }
            )

        if len(created_nodes) != 132:
            raise ValueError("创建组织节点数量异常，事务已回滚")
        rollback = {
            "strategy": "DEACTIVATE_GROUPS_THEN_CLASSES",
            "requires_zero_member_relations": True,
            "created_nodes": created_nodes,
        }
        cursor = execute(
            connection,
            "INSERT INTO import_batches"
            "(import_type, source_name, source_sha256, status, preview_json, "
            "created_by, created_at, applied_at) "
            "VALUES (?, ?, ?, 'APPLIED', ?, ?, ?, ?)",
            (
                IMPORT_TYPE,
                source_name,
                source_sha256,
                json.dumps(
                    {
                        "phase": "ORGANIZATION_ONLY",
                        "created_classes": 20,
                        "created_groups": 112,
                        "members_changed": 0,
                        "rollback": rollback,
                    },
                    ensure_ascii=False,
                ),
                actor_user_id,
                now,
                now,
            ),
        )
        batch_id = cursor.lastrowid
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="class_roster_org_import.apply",
            resource_type="import_batch",
            resource_id=str(batch_id),
            after={
                "source_sha256": source_sha256,
                "created_classes": 20,
                "created_groups": 112,
                "members_changed": 0,
            },
        )
    return {
        "batch_id": batch_id,
        "status": "APPLIED",
        "created_classes": 20,
        "created_groups": 112,
        "members_changed": 0,
    }
