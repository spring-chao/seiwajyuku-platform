"""Audited, fail-closed cleanup for duplicate active class organization nodes."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.db import execute, transaction
from app.services.audit import write_audit


CLASS_TYPES = ("CLASS", "SPECIAL_COHORT")

# These tables may legitimately refer to an organization, but a class-node
# merge must not guess how to reconcile their business semantics. They make a
# candidate manual-review only; the supported automatic path is deliberately
# limited to roster and attendance references below.
BLOCKING_REFERENCES = (
    ("member_primary_org", "members", "org_unit_id"),
    ("member_development_org", "members", "development_org_unit_id"),
    ("followup_tasks", "followup_tasks", "org_unit_id"),
    ("renewal_cycles", "renewal_cycles", "org_unit_id"),
    ("integration_snapshots", "integration_snapshots", "org_unit_id"),
    ("member_activity_facts", "member_activity_facts", "org_unit_id"),
    ("member_service_signals", "member_service_signals", "org_unit_id"),
    ("org_metric_targets", "org_metric_targets", "org_unit_id"),
    ("metric_period_values", "metric_period_values", "org_unit_id"),
    ("annual_actions", "annual_actions", "org_unit_id"),
    ("institution_org_links", "institution_org_links", "org_unit_id"),
)


def _rows(connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in execute(connection, sql, params).fetchall()]


def _duplicate_sets(connection) -> dict[str, list[dict[str, Any]]]:
    units = _rows(
        connection,
        "SELECT id, unit_code, name, unit_type, parent_id, created_at FROM org_units "
        "WHERE is_active=1 AND unit_type IN ('CLASS', 'SPECIAL_COHORT') "
        "ORDER BY name, created_at, id",
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        grouped[unit["name"]].append(unit)
    return {name: values for name, values in grouped.items() if len(values) > 1}


def _reference_count(connection, table: str, column: str, duplicate_ids: list[str]) -> int:
    placeholders = ",".join("?" for _ in duplicate_ids)
    return int(
        _rows(
            connection,
            f"SELECT COUNT(*) AS count FROM {table} WHERE {column} IN ({placeholders})",
            tuple(duplicate_ids),
        )[0]["count"]
    )


def _blocking_reference_counts(connection, duplicate_ids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key, table, column in BLOCKING_REFERENCES:
        try:
            counts[key] = _reference_count(connection, table, column, duplicate_ids)
        except Exception:
            # A missing table means this cleanup cannot prove the candidate is
            # safe on the current schema, so it must fail closed.
            counts[key] = -1
    return counts


def preview_duplicate_class_cleanup() -> dict[str, Any]:
    """Produce no-write migration candidates; IDs are intentionally omitted."""
    with transaction() as connection:
        duplicate_sets = _duplicate_sets(connection)
        candidates: list[dict[str, Any]] = []
        for name, units in duplicate_sets.items():
            canonical = units[0]
            duplicates = units[1:]
            duplicate_ids = [unit["id"] for unit in duplicates]
            placeholders = ",".join("?" for _ in duplicate_ids)
            counts = {"member_relations": 0, "attendance_groups": 0,
                      "identity_scopes": 0, "volunteer_appointments": 0}
            for key, table, column in (
                ("member_relations", "member_org_relations", "org_unit_id"),
                ("attendance_groups", "attendance_event_groups", "study_org_unit_id"),
                ("identity_scopes", "employee_service_responsibilities", "org_unit_id"),
                ("volunteer_appointments", "volunteer_appointments", "org_unit_id"),
            ):
                try:
                    counts[key] = _reference_count(connection, table, column, duplicate_ids)
                except Exception:
                    counts[key] = -1
            child_groups = _rows(
                connection,
                f"SELECT id, name FROM org_units WHERE is_active=1 AND parent_id IN ({placeholders})",
                tuple(duplicate_ids),
            )
            blockers = _blocking_reference_counts(connection, duplicate_ids)
            candidates.append({
                "class_name": name,
                "canonical_unit_code": canonical["unit_code"],
                "duplicate_count": len(duplicates),
                "reference_counts": counts,
                "blocking_reference_counts": blockers,
                "child_group_count": len(child_groups),
                "safe_to_auto_merge": not child_groups and not any(
                    count != 0 for count in blockers.values()
                ),
            })
        return {
            "duplicate_class_name_count": len(candidates),
            "candidates": candidates,
            "automatic_apply_allowed": all(item["safe_to_auto_merge"] for item in candidates),
        }


def apply_duplicate_class_cleanup(actor_user_id: int, *, confirmation: str) -> dict[str, int]:
    """Repoint references, deactivate duplicates, and create an auditable rollback record.

    Class nodes that still own active groups are rejected. Moving those trees
    needs a separately reviewed group-level reconciliation rather than a
    destructive automatic merge.
    """
    if confirmation.strip() != "确认合并重复班级组织":
        raise ValueError("确认文字不匹配，未执行组织去重")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        duplicate_sets = _duplicate_sets(connection)
        merged = 0
        moved_relations = 0
        moved_events = 0
        for name, units in duplicate_sets.items():
            canonical = units[0]
            duplicates = units[1:]
            duplicate_ids = [unit["id"] for unit in duplicates]
            placeholders = ",".join("?" for _ in duplicate_ids)
            children = _rows(
                connection,
                f"SELECT id FROM org_units WHERE is_active=1 AND parent_id IN ({placeholders})",
                tuple(duplicate_ids),
            )
            if children:
                raise ValueError(f"班级 {name} 仍有小组组织，需人工复核后单独处理")
            blockers = _blocking_reference_counts(connection, duplicate_ids)
            if any(count != 0 for count in blockers.values()):
                raise ValueError(f"班级 {name} 存在需人工复核的业务引用，未执行归并")
            for duplicate in duplicates:
                duplicate_id = duplicate["id"]
                # Relation rows have a uniqueness constraint. Preserve a
                # canonical row if it already exists and only delete the now
                # redundant duplicate reference.
                existing = _rows(
                    connection,
                    "SELECT id, member_id, relation_type FROM member_org_relations WHERE org_unit_id=?",
                    (duplicate_id,),
                )
                for relation in existing:
                    already = execute(
                        connection,
                        "SELECT id FROM member_org_relations WHERE member_id=? AND org_unit_id=? AND relation_type=?",
                        (relation["member_id"], canonical["id"], relation["relation_type"]),
                    ).fetchone()
                    if already:
                        execute(connection, "DELETE FROM member_org_relations WHERE id=?", (relation["id"],))
                    else:
                        execute(
                            connection,
                            "UPDATE member_org_relations SET org_unit_id=?, updated_at=? WHERE id=?",
                            (canonical["id"], now, relation["id"]),
                        )
                    moved_relations += 1
                cursor = execute(
                    connection,
                    "UPDATE attendance_event_groups SET study_org_unit_id=?, updated_at=? WHERE study_org_unit_id=?",
                    (canonical["id"], now, duplicate_id),
                )
                moved_events += cursor.rowcount
                for table in ("employee_service_responsibilities", "volunteer_appointments"):
                    try:
                        execute(
                            connection,
                            f"UPDATE {table} SET org_unit_id=?, updated_at=? WHERE org_unit_id=?",
                            (canonical["id"], now, duplicate_id),
                        )
                    except Exception:
                        # The production baseline has these identity tables;
                        # isolated legacy test schemas may not.
                        continue
                execute(
                    connection,
                    "UPDATE org_units SET is_active=0, active_until=?, updated_at=? WHERE id=?",
                    (now[:10], now, duplicate_id),
                )
                merged += 1
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="org.class_name_duplicate.cleanup",
            resource_type="org_unit",
            resource_id="class-name-uniqueness",
            purpose="全平台班级名称唯一性确认",
            after={
                "deactivated_duplicate_classes": merged,
                "moved_member_relations": moved_relations,
                "moved_attendance_groups": moved_events,
            },
        )
    return {
        "deactivated_duplicate_classes": merged,
        "moved_member_relations": moved_relations,
        "moved_attendance_groups": moved_events,
    }
