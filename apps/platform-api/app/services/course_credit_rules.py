from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit


DEFAULT_PLAN_KEY = "STANDARD_3Y_2026"
DEFAULT_VERSION_LABEL = "2026.1"
AVAILABLE_CREDIT_POINTS = [0, 15, 20, 30, 40]
_COURSE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _catalog_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        path = parent / "data" / "learning-plans" / "course-credit-catalog-2026.json"
        if path.is_file():
            return path
    raise FileNotFoundError("找不到课程积分配置目录")


def _catalog(plan_key: str) -> list[dict[str, Any]]:
    payload = json.loads(_catalog_path().read_text(encoding="utf-8"))
    if payload.get("plan_key") != plan_key:
        raise ValueError("课程积分目录与学习计划不匹配")
    return list(payload.get("entries", []))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _version(plan_key: str, version_label: str) -> dict[str, Any] | None:
    try:
        return fetch_one(
            "SELECT id, plan_key, version_label, status, based_on_version_label, "
            "created_by, created_at, updated_at FROM learning_plan_credit_rule_versions "
            "WHERE plan_key=? AND version_label=?",
            (plan_key, version_label),
        )
    except Exception as exc:
        # Read-only deployments may be rolled out before the migration window.
        # Keep the configuration page useful from the immutable catalog while
        # refusing writes until the schema is available.
        if "no such table" in str(exc).lower() or "doesn't exist" in str(exc).lower():
            return None
        raise


def _decode_aliases(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        decoded = json.loads(value or "[]")
        return [str(item) for item in decoded] if isinstance(decoded, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def _rule_payload(row: dict[str, Any], *, persisted: bool = True) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "course_key": row["course_key"],
        "course_name": row["course_name"],
        "year_index": row.get("year_index"),
        "credit_points": int(row.get("credit_points") or 0),
        "status": row.get("status") or "PENDING",
        "source": row.get("source") or "SYSTEM_DEFAULT",
        "aliases": _decode_aliases(row.get("aliases_json", row.get("aliases", []))),
        "persisted": persisted,
        "updated_at": row.get("updated_at"),
    }


def _catalog_row(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": None,
        "course_key": entry["course_key"],
        "course_name": entry["course_name"],
        "year_index": entry.get("year_index"),
        "credit_points": entry.get("credit_points", 0),
        "status": entry.get("status", "PENDING"),
        "source": entry.get("source", "SYSTEM_DEFAULT"),
        "aliases": entry.get("aliases", []),
        "persisted": False,
        "updated_at": None,
    }


def _rule_rows(version_id: int) -> list[dict[str, Any]]:
    try:
        return fetch_all(
            "SELECT id, course_key, course_name, year_index, credit_points, status, source, "
            "aliases_json, updated_at FROM learning_plan_credit_rules "
            "WHERE rule_version_id=? ORDER BY year_index IS NULL, year_index, course_name, course_key",
            (version_id,),
        )
    except Exception as exc:
        if "no such table" in str(exc).lower() or "doesn't exist" in str(exc).lower():
            return []
        raise


def _credit_schema_available() -> bool:
    try:
        fetch_one("SELECT id FROM learning_plan_credit_rule_versions LIMIT 1")
        return True
    except Exception as exc:
        if "no such table" in str(exc).lower() or "doesn't exist" in str(exc).lower():
            return False
        raise


def list_course_credit_rules(
    plan_key: str = DEFAULT_PLAN_KEY,
    version_label: str = DEFAULT_VERSION_LABEL,
) -> dict[str, Any]:
    entries = _catalog(plan_key)
    storage_available = _credit_schema_available()
    version = _version(plan_key, version_label)
    rows = _rule_rows(int(version["id"])) if version else []
    by_key = {row["course_key"]: _rule_payload(row) for row in rows}
    for entry in entries:
        by_key.setdefault(entry["course_key"], _catalog_row(entry))
    # Persisted custom courses remain visible even after the catalog file is no longer
    # the source of truth for a published version.
    result = sorted(
        by_key.values(),
        key=lambda row: (row["year_index"] is None, row["year_index"] or 999, row["course_name"]),
    )
    return {
        "plan_key": plan_key,
        "version_label": version_label,
        "version_status": version["status"] if version else "DRAFT",
        "persisted": bool(version and rows),
        "based_on_version_label": version.get("based_on_version_label") if version else "2026",
        "available_credit_points": AVAILABLE_CREDIT_POINTS,
        "custom_credit_allowed": True,
        "storage_available": storage_available,
        "can_edit": storage_available and (not version or version["status"] == "DRAFT"),
        "rules": result,
    }


def _insert_catalog_rows(connection, version_id: int, entries: list[dict[str, Any]], actor_user_id: int | None) -> None:
    now = _now()
    for entry in entries:
        execute(
            connection,
            "INSERT OR IGNORE INTO learning_plan_credit_rules "
            "(rule_version_id, course_key, course_name, year_index, credit_points, status, source, aliases_json, "
            "created_by, updated_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            if connection.__class__.__module__ == "sqlite3" else
            "INSERT IGNORE INTO learning_plan_credit_rules "
            "(rule_version_id, course_key, course_name, year_index, credit_points, status, source, aliases_json, "
            "created_by, updated_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_id,
                entry["course_key"],
                entry["course_name"],
                entry.get("year_index"),
                int(entry.get("credit_points", 0)),
                entry.get("status", "PENDING"),
                entry.get("source", "SYSTEM_DEFAULT"),
                json.dumps(entry.get("aliases", []), ensure_ascii=False),
                actor_user_id,
                actor_user_id,
                now,
                now,
            ),
        )


def _ensure_draft(connection, plan_key: str, version_label: str, actor_user_id: int | None, based_on: str | None = None) -> dict[str, Any]:
    row = execute(
        connection,
        "SELECT id, plan_key, version_label, status, based_on_version_label, created_by, created_at, updated_at "
        "FROM learning_plan_credit_rule_versions WHERE plan_key=? AND version_label=?",
        (plan_key, version_label),
    ).fetchone()
    if row:
        version = dict(row)
        if version["status"] != "DRAFT":
            raise ValueError("已发布或已归档版本不可修改，请创建新的候选版本")
    else:
        now = _now()
        cursor = execute(
            connection,
            "INSERT INTO learning_plan_credit_rule_versions "
            "(plan_key, version_label, status, based_on_version_label, created_by, created_at, updated_at) "
            "VALUES (?, ?, 'DRAFT', ?, ?, ?, ?)",
            (plan_key, version_label, based_on or "2026", actor_user_id, now, now),
        )
        version = {
            "id": cursor.lastrowid,
            "plan_key": plan_key,
            "version_label": version_label,
            "status": "DRAFT",
            "based_on_version_label": based_on or "2026",
        }
    _insert_catalog_rows(connection, int(version["id"]), _catalog(plan_key), actor_user_id)
    return version


def update_course_credit_rule(
    *,
    actor_user_id: int,
    plan_key: str,
    version_label: str,
    course_key: str,
    credit_points: int,
    status: str = "CONFIGURED",
    course_name: str | None = None,
    year_index: int | None = None,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    if not _COURSE_KEY_RE.fullmatch(course_key):
        raise ValueError("课程标识格式不正确")
    if not isinstance(credit_points, int) or isinstance(credit_points, bool) or not 0 <= credit_points <= 999:
        raise ValueError("积分必须是0到999之间的整数")
    if status not in {"PENDING", "CONFIGURED"}:
        raise ValueError("课程积分状态不正确")
    if status == "PENDING" and credit_points != 0:
        raise ValueError("待配置课程的积分必须为0")
    if status == "CONFIGURED" and credit_points < 0:
        raise ValueError("已配置课程积分不能为负数")
    with transaction() as connection:
        version = _ensure_draft(connection, plan_key, version_label, actor_user_id)
        current = execute(
            connection,
            "SELECT id, course_key, course_name, year_index, credit_points, status, source, aliases_json, updated_at "
            "FROM learning_plan_credit_rules WHERE rule_version_id=? AND course_key=?",
            (int(version["id"]), course_key),
        ).fetchone()
        before = _rule_payload(dict(current)) if current else None
        if not current and not course_name:
            raise ValueError("新增课程必须填写课程名称")
        now = _now()
        final_name = course_name or dict(current)["course_name"]
        final_year = year_index if year_index is not None else (dict(current).get("year_index") if current else None)
        final_aliases = aliases if aliases is not None else (_decode_aliases(dict(current).get("aliases_json")) if current else [])
        if current:
            execute(
                connection,
                "UPDATE learning_plan_credit_rules SET course_name=?, year_index=?, credit_points=?, status=?, source='OPERATIONS', aliases_json=?, updated_by=?, updated_at=? WHERE id=?",
                (final_name, final_year, credit_points, status, json.dumps(final_aliases, ensure_ascii=False), actor_user_id, now, dict(current)["id"]),
            )
        else:
            execute(
                connection,
                "INSERT INTO learning_plan_credit_rules (rule_version_id, course_key, course_name, year_index, credit_points, status, source, aliases_json, created_by, updated_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'OPERATIONS', ?, ?, ?, ?, ?)",
                (int(version["id"]), course_key, final_name, final_year, credit_points, status, json.dumps(final_aliases, ensure_ascii=False), actor_user_id, actor_user_id, now, now),
            )
        after_row = execute(
            connection,
            "SELECT id, course_key, course_name, year_index, credit_points, status, source, aliases_json, updated_at FROM learning_plan_credit_rules WHERE rule_version_id=? AND course_key=?",
            (int(version["id"]), course_key),
        ).fetchone()
        after = _rule_payload(dict(after_row))
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="learning_plan.credit_rule.update",
            resource_type="learning_plan_credit_rule",
            resource_id=f"{plan_key}:{version_label}:{course_key}",
            purpose="维护课程积分标准",
            before=before,
            after=after,
        )
        updated_rule = after
    return {
        **list_course_credit_rules(plan_key, version_label),
        "updated_rule": updated_rule,
    }


def create_course_credit_rule_version(
    *, actor_user_id: int, plan_key: str, version_label: str, based_on_version_label: str = DEFAULT_VERSION_LABEL
) -> dict[str, Any]:
    if not version_label or len(version_label) > 64:
        raise ValueError("版本号不能为空且不能超过64个字符")
    with transaction() as connection:
        exists = execute(
            connection,
            "SELECT id FROM learning_plan_credit_rule_versions WHERE plan_key=? AND version_label=?",
            (plan_key, version_label),
        ).fetchone()
        if exists:
            raise ValueError("该课程积分版本已存在")
        source = execute(
            connection,
            "SELECT id, status FROM learning_plan_credit_rule_versions WHERE plan_key=? AND version_label=?",
            (plan_key, based_on_version_label),
        ).fetchone()
        now = _now()
        cursor = execute(
            connection,
            "INSERT INTO learning_plan_credit_rule_versions (plan_key, version_label, status, based_on_version_label, created_by, created_at, updated_at) VALUES (?, ?, 'DRAFT', ?, ?, ?, ?)",
            (plan_key, version_label, based_on_version_label, actor_user_id, now, now),
        )
        version_id = cursor.lastrowid
        if source:
            rows = execute(
                connection,
                "SELECT course_key, course_name, year_index, credit_points, status, source, aliases_json FROM learning_plan_credit_rules WHERE rule_version_id=?",
                (int(source["id"]),),
            ).fetchall()
            for row in rows:
                execute(
                    connection,
                    "INSERT INTO learning_plan_credit_rules (rule_version_id, course_key, course_name, year_index, credit_points, status, source, aliases_json, created_by, updated_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (version_id, row["course_key"], row["course_name"], row["year_index"], row["credit_points"], row["status"], row["source"], row["aliases_json"], actor_user_id, actor_user_id, now, now),
                )
        else:
            _insert_catalog_rows(connection, int(version_id), _catalog(plan_key), actor_user_id)
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="learning_plan.credit_rule_version.create",
            resource_type="learning_plan_credit_rule_version",
            resource_id=f"{plan_key}:{version_label}",
            purpose="创建新的课程积分候选版本",
            after={"plan_key": plan_key, "version_label": version_label, "based_on_version_label": based_on_version_label},
        )
    return list_course_credit_rules(plan_key, version_label)
