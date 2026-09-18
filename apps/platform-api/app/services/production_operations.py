"""Bounded production operations that are not a general SQL interface.

Only the registered G5.4 course-rule reconciliation is implemented here. It
owns one database connection from the operation lock through commit/rollback;
callers cannot provide SQL, table names, lock names, or an alternate action.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core.build_info import get_build_info
from app.core.settings import get_settings
from app.db import execute, transaction
from app.services.audit import write_audit
from app.services.course_credit_canonical import (
    CANONICAL_PLAN_KEY,
    CANONICAL_VERSION_LABEL,
    EXPECTED_RULE_COUNT,
    canonical_fingerprint,
    load_canonical_policy,
)
from app.services.course_credit_reconciliation import (
    UNUSED_PLACEHOLDER_KEYS,
    guarded_apply,
    production_fingerprint,
    reconcile_course_rules,
)


OPERATION_KEY = "G5_4_RECONCILE_COURSE_RULES_2026"
OPERATION_LOCK_NAME = "seiwajyuku:g5.4:course-rule-reconciliation:2026"
REQUIRED_PERMISSION = "plans:production_rule_reconciliation_apply"
EXPECTED_BEFORE_RULE_COUNT = 14
EXPECTED_ACTIVE_BINDINGS = 19
EXPECTED_CHANGE_COUNTS = {"KEEP": 7, "UPDATE": 4, "ADD": 14, "REMOVE_UNUSED_PLACEHOLDER": 3}
EXPECTED_GENERIC_RULES = {
    "DAILY_READING": ("DAILY_ONCE", Decimal("1"), None),
    "EXCELLENT_SHARE": ("MONTHLY_CAP", Decimal("1"), Decimal("5")),
    "GROUP_MEETING_ATTENDANCE": ("CYCLE_ONCE", Decimal("4"), None),
    "COURSE_COMPLETION": ("COURSE_COMPLETION", None, None),
    "CLASS_MEETING_SCORE": ("EVENT_ONCE", None, None),
    "MANUAL_ADJUSTMENT": ("MANUAL_ADJUSTMENT", None, None),
}

_sqlite_operation_lock = threading.Lock()


class ProductionOperationError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _fail(code: str, message: str, *, status_code: int = 409) -> None:
    raise ProductionOperationError(code, message, status_code=status_code)


def _is_sqlite(connection: Any) -> bool:
    return isinstance(connection, sqlite3.Connection)


def _locking(statement: str, connection: Any) -> str:
    return statement if _is_sqlite(connection) else f"{statement} FOR UPDATE"


def _rows(connection: Any, statement: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in execute(connection, statement, params).fetchall()]


def _row(connection: Any, statement: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    value = execute(connection, statement, params).fetchone()
    return dict(value) if value else None


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _acquire_operation_lock(connection: Any) -> bool:
    if _is_sqlite(connection):
        return _sqlite_operation_lock.acquire(blocking=False)
    acquired = _row(connection, "SELECT GET_LOCK(?, 0) AS acquired", (OPERATION_LOCK_NAME,))
    return bool(acquired and int(acquired["acquired"]) == 1)


def _verify_runtime_gates(expected_release_commit: str) -> str:
    settings = get_settings()
    if not settings.g5_4_production_rule_apply_enabled:
        _fail("FEATURE_DISABLED", "Not found", status_code=404)
    if settings.app_env not in {"production", "test"}:
        _fail("ENVIRONMENT_NOT_ALLOWED", "该操作只允许 production 或隔离 test 环境")
    if settings.deployment_read_only:
        _fail("DEPLOYMENT_READ_ONLY", "部署只读门禁仍处于开启状态", status_code=403)
    if not settings.allow_production_mutations:
        _fail("PRODUCTION_MUTATIONS_DISABLED", "生产写入总门禁未开启", status_code=403)
    if settings.learning_credit_settlement_enabled:
        _fail("SETTLEMENT_ENABLED", "学分正式结算门禁必须保持关闭")
    runtime_commit = str(get_build_info().get("commit_sha") or "").strip()
    if runtime_commit != expected_release_commit:
        _fail("RELEASE_COMMIT_MISMATCH", "运行版本与批准的 release commit 不一致")
    return runtime_commit


def _verify_actor(connection: Any, actor_user_id: int) -> None:
    actor = _row(
        connection,
        "SELECT u.id FROM app_users u JOIN user_roles ur ON ur.user_id=u.id "
        "JOIN roles r ON r.role_key=ur.role_key AND r.is_active=1 "
        "WHERE u.id=? AND u.is_active=1 AND ur.role_key='system_admin'",
        (actor_user_id,),
    )
    if not actor:
        _fail("PERMISSION_DENIED", "仅 system_admin 可执行该生产操作", status_code=403)


def _read_version(connection: Any) -> dict[str, Any]:
    rows = _rows(
        connection,
        _locking(
            "SELECT id, plan_key, version_label, status, based_on_version_label, "
            "created_by, created_at, updated_at FROM learning_plan_credit_rule_versions "
            "WHERE plan_key=? AND version_label=?",
            connection,
        ),
        (CANONICAL_PLAN_KEY, CANONICAL_VERSION_LABEL),
    )
    if len(rows) != 1:
        _fail("RULE_STATE_UNEXPECTED", "目标课程规则版本必须恰好存在一条")
    return rows[0]


def _read_rules(connection: Any, version_id: int) -> list[dict[str, Any]]:
    return _rows(
        connection,
        _locking(
            "SELECT id, rule_version_id, course_key, course_name, year_index, "
            "credit_points, status, source, aliases_json, created_by, updated_by, "
            "created_at, updated_at FROM learning_plan_credit_rules "
            "WHERE rule_version_id=? ORDER BY course_key, id",
            connection,
        ),
        (version_id,),
    )


def _verify_generic_rules(connection: Any) -> None:
    versions = _rows(
        connection,
        _locking(
            "SELECT id, status FROM learning_credit_rule_versions "
            "WHERE rule_set_key=? AND version_label=?",
            connection,
        ),
        (CANONICAL_PLAN_KEY, CANONICAL_VERSION_LABEL),
    )
    if len(versions) != 1 or versions[0]["status"] != "PUBLISHED":
        _fail("GENERIC_RULE_STATE_UNEXPECTED", "通用学分规则版本不是唯一 PUBLISHED 基线")
    rules = _rows(
        connection,
        _locking(
            "SELECT rule_key, settlement_model, points, cap_points, status "
            "FROM learning_credit_rules WHERE rule_version_id=? ORDER BY rule_key",
            connection,
        ),
        (versions[0]["id"],),
    )
    actual = {
        row["rule_key"]: (
            row["settlement_model"],
            None if row["points"] is None else _decimal(row["points"]),
            None if row["cap_points"] is None else _decimal(row["cap_points"]),
        )
        for row in rules
        if row["status"] == "ACTIVE"
    }
    if len(rules) != 6 or len(actual) != 6 or actual != EXPECTED_GENERIC_RULES:
        _fail("GENERIC_RULE_STATE_UNEXPECTED", "通用学分规则不是批准的 6 条 ACTIVE 基线")


def _verify_followup_migrations_not_applied(connection: Any) -> None:
    rows = _rows(
        connection,
        "SELECT version FROM schema_migrations WHERE version IN (?, ?, ?)",
        (
            "0064_fix_credit_rule_mapping_and_binding_freeze.sql",
            "0065_learning_credit_history_import.sql",
            "0066_historical_credit_time_precision_review.sql",
        ),
    )
    if rows:
        _fail("RULE_STATE_UNEXPECTED", "0064/0065/0066 必须仍为 NOT_APPLIED")


def _verify_mapping_and_bindings(connection: Any) -> dict[str, int]:
    mapping = _row(
        connection,
        "SELECT COUNT(*) AS n FROM learning_plan_credit_rule_mappings "
        "WHERE plan_key='standard-3y' AND plan_version_label='2026'",
    )
    if int(mapping["n"] if mapping else -1) != 0:
        _fail("MAPPING_STATE_UNEXPECTED", "0064 前规则映射必须为 0")
    binding = _row(
        connection,
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN b.credit_rule_version_id IS NOT NULL THEN 1 ELSE 0 END) AS generic_frozen, "
        "SUM(CASE WHEN b.course_credit_rule_version_id IS NOT NULL THEN 1 ELSE 0 END) AS course_frozen "
        "FROM class_learning_bindings b JOIN learning_plan_versions p ON p.id=b.plan_version_id "
        "WHERE b.status='ACTIVE' AND p.plan_key='standard-3y' AND p.version_label='2026'",
    )
    summary = {
        "total": int(binding["total"] or 0),
        "generic_frozen": int(binding["generic_frozen"] or 0),
        "course_frozen": int(binding["course_frozen"] or 0),
    }
    if summary != {"total": EXPECTED_ACTIVE_BINDINGS, "generic_frozen": 0, "course_frozen": 0}:
        _fail("BINDING_STATE_UNEXPECTED", "0064 前绑定必须为 0/19 双规则冻结")
    return summary


def _read_ledger(connection: Any) -> dict[str, Any]:
    row = _row(
        connection,
        _locking(
            "SELECT COUNT(*) AS entry_count, COALESCE(SUM(points), 0) AS points "
            "FROM learning_credit_entries",
            connection,
        ),
    )
    return {"entry_count": int(row["entry_count"]), "points": _decimal(row["points"])}


def _verify_empty_ledger(connection: Any) -> dict[str, Any]:
    ledger = _read_ledger(connection)
    if ledger["entry_count"] != 0 or ledger["points"] != Decimal("0"):
        _fail("LEDGER_NOT_EMPTY", "learning_credit_entries 必须保持 0 条 / 0 分")
    if get_settings().learning_credit_settlement_enabled:
        _fail("SETTLEMENT_ENABLED", "事务校验时学分正式结算门禁已开启")
    return ledger


def _reference_counts(connection: Any, version_id: int) -> dict[str, dict[str, int]]:
    mapping = _row(
        connection,
        "SELECT COUNT(*) AS generic_count, "
        "SUM(CASE WHEN course_credit_rule_version_id=? THEN 1 ELSE 0 END) AS course_count "
        "FROM learning_plan_credit_rule_mappings "
        "WHERE generic_rule_version_id IN (SELECT id FROM learning_credit_rule_versions "
        "WHERE rule_set_key=? AND version_label=?) OR course_credit_rule_version_id=?",
        (version_id, CANONICAL_PLAN_KEY, CANONICAL_VERSION_LABEL, version_id),
    )
    generic_mapping = int(mapping["generic_count"] or 0) if mapping else 0
    course_mapping = int(mapping["course_count"] or 0) if mapping else 0
    result: dict[str, dict[str, int]] = {}
    for key in sorted(UNUSED_PLACEHOLDER_KEYS):
        study = _row(
            connection,
            "SELECT "
            "(SELECT COUNT(*) FROM study_meeting_sessions WHERE course_key=?) + "
            "(SELECT COUNT(*) FROM study_meeting_courses WHERE course_key=?) AS n",
            (key, key),
        )
        completion = _row(
            connection,
            "SELECT COUNT(*) AS n FROM study_meeting_course_completions c "
            "JOIN study_meeting_courses sc ON sc.id=c.study_meeting_course_id "
            "WHERE sc.course_key=?",
            (key,),
        )
        ledger = _row(
            connection,
            "SELECT COUNT(*) AS n FROM learning_credit_entries WHERE rule_key=?",
            (key,),
        )
        counts = {
            "generic_rule_mapping": generic_mapping,
            "course_rule_mapping": course_mapping,
            "study_meeting_course_reference": int(study["n"] or 0),
            "completion_fact": int(completion["n"] or 0),
            "ledger_reference": int(ledger["n"] or 0),
        }
        counts["all_reference_count"] = sum(counts.values())
        result[key] = counts
    return result


def _change_counts(plan: list[dict[str, Any]]) -> dict[str, int]:
    return {
        action: sum(1 for item in plan if item["action"] == action)
        for action in EXPECTED_CHANGE_COUNTS
    }


def _is_already_applied(reconciliation: dict[str, Any]) -> bool:
    return (
        reconciliation["production_rule_count"] == EXPECTED_RULE_COUNT
        and reconciliation["exact_match_count"] == EXPECTED_RULE_COUNT
        and reconciliation["missing_count"] == 0
        and reconciliation["different_count"] == 0
        and reconciliation["extra_count"] == 0
        and reconciliation["conflict_count"] == 0
    )


def _write_plan(
    connection: Any,
    *,
    version_id: int,
    actor_user_id: int,
    reason: str,
    release_commit: str,
    plan: list[dict[str, Any]],
    fault_injector: Callable[[str, int], None] | None,
) -> None:
    changed = 0
    per_action = {"UPDATE": 0, "ADD": 0, "REMOVE_UNUSED_PLACEHOLDER": 0}
    now = datetime.now(UTC).replace(tzinfo=None)
    for item in plan:
        action = item["action"]
        if action == "KEEP":
            continue
        if action == "UPDATE":
            after = item["after"]
            execute(
                connection,
                "UPDATE learning_plan_credit_rules SET course_name=?, year_index=?, "
                "credit_points=?, status=?, source=?, aliases_json=?, updated_by=?, updated_at=? "
                "WHERE id=? AND rule_version_id=?",
                (
                    after["course_name"], after["year_index"], after["credit_points"],
                    after["status"], after["source"],
                    json.dumps(after["aliases"], ensure_ascii=False, separators=(",", ":")),
                    actor_user_id, now, item["before"]["id"], version_id,
                ),
            )
        elif action == "ADD":
            after = item["after"]
            execute(
                connection,
                "INSERT INTO learning_plan_credit_rules "
                "(rule_version_id, course_key, course_name, year_index, credit_points, status, "
                "source, aliases_json, created_by, updated_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version_id, after["course_key"], after["course_name"], after["year_index"],
                    after["credit_points"], after["status"], after["source"],
                    json.dumps(after["aliases"], ensure_ascii=False, separators=(",", ":")),
                    actor_user_id, actor_user_id, now, now,
                ),
            )
        elif action == "REMOVE_UNUSED_PLACEHOLDER":
            execute(
                connection,
                "DELETE FROM learning_plan_credit_rules WHERE id=? AND rule_version_id=?",
                (item["before"]["id"], version_id),
            )
        else:
            _fail("RULE_STATE_UNEXPECTED", f"不允许执行计划动作 {action}")
        per_action[action] += 1
        changed += 1
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action=f"production.g5_4.course_rule_reconciliation.{action.lower()}",
            resource_type="learning_plan_credit_rule",
            resource_id=str(item["before"]["id"]) if item.get("before") else item["course_key"],
            purpose=reason,
            before=item.get("before"),
            after=item.get("after"),
            request_id=release_commit,
        )
        if fault_injector:
            fault_injector(action, per_action[action])
    if changed != 21:
        _fail("FINAL_VALIDATION_FAILED", "批准计划必须恰好产生 21 条业务变更")


def apply_g5_4_course_rule_reconciliation(
    *,
    expected_release_commit: str,
    expected_production_fingerprint: str,
    expected_canonical_fingerprint: str,
    expected_course_rule_version_id: int,
    expected_course_rule_status: str,
    expected_rule_count: int,
    expected_placeholder_keys: list[str],
    execution_reason: str,
    actor_user_id: int,
    fault_injector: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """Atomically reconcile only the registered G5.4 course-rule operation."""

    release_commit = _verify_runtime_gates(expected_release_commit)
    if expected_course_rule_status != "DRAFT" or expected_rule_count != EXPECTED_BEFORE_RULE_COUNT:
        _fail("RULE_STATE_UNEXPECTED", "请求中的批准前状态必须是 14 条 DRAFT")
    if set(expected_placeholder_keys) != set(UNUSED_PLACEHOLDER_KEYS) or len(expected_placeholder_keys) != 3:
        _fail("RULE_STATE_UNEXPECTED", "placeholder keys 必须与固定白名单完全一致")
    if not execution_reason.strip():
        _fail("RULE_STATE_UNEXPECTED", "execution_reason 不能为空")

    sqlite_lock_acquired = False
    try:
        with transaction() as connection:
            if not _is_sqlite(connection):
                execute(connection, "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            if not _acquire_operation_lock(connection):
                _fail("APPLY_ALREADY_RUNNING", "同一规则收口操作正在执行")
            sqlite_lock_acquired = _is_sqlite(connection)
            _verify_actor(connection, actor_user_id)

            # Re-evaluate process state only after owning the operation lock.
            if _verify_runtime_gates(expected_release_commit) != release_commit:
                _fail("RELEASE_COMMIT_MISMATCH", "运行版本在事务开始前发生变化")
            policy = load_canonical_policy()
            canonical_fp = canonical_fingerprint(policy)
            if canonical_fp != expected_canonical_fingerprint:
                _fail("CANONICAL_FINGERPRINT_MISMATCH", "运行代码中的 canonical fingerprint 已变化")

            version = _read_version(connection)
            if int(version["id"]) != expected_course_rule_version_id:
                _fail("COURSE_RULE_VERSION_MISMATCH", "课程规则版本 ID 与批准基线不一致")
            if version["status"] != expected_course_rule_status:
                _fail("RULE_STATE_UNEXPECTED", "课程规则版本状态不是批准的 DRAFT")
            if version.get("based_on_version_label") != "2026":
                _fail("RULE_STATE_UNEXPECTED", "课程规则 based_on_version_label 不是 2026")

            _verify_generic_rules(connection)
            _verify_followup_migrations_not_applied(connection)
            ledger_before = _verify_empty_ledger(connection)
            rules = _read_rules(connection, int(version["id"]))
            references = _reference_counts(connection, int(version["id"]))
            if any(value["all_reference_count"] != 0 for value in references.values()):
                _fail("PLACEHOLDER_REFERENCE_FOUND", "占位规则出现引用，禁止删除")
            bindings = _verify_mapping_and_bindings(connection)
            reconciliation = reconcile_course_rules(
                version=version,
                production_rules=rules,
                policy=policy,
                reference_counts=references,
            )

            if _is_already_applied(reconciliation):
                return {
                    "operation": OPERATION_KEY,
                    "status": "ALREADY_APPLIED",
                    "release_commit": release_commit,
                    "before": {"rule_count": EXPECTED_RULE_COUNT, "fingerprint": canonical_fp},
                    "changes": {"keep": EXPECTED_RULE_COUNT, "update": 0, "add": 0, "remove": 0},
                    "after": {"rule_count": EXPECTED_RULE_COUNT, "fingerprint": canonical_fp},
                    "ledger_delta": 0,
                    "settlement_enabled": False,
                }
            if len(rules) != EXPECTED_BEFORE_RULE_COUNT:
                _fail("UNKNOWN_INTERMEDIATE_STATE", "规则数量既不是批准前 14 条，也不是 canonical 25 条")
            if reconciliation["production_fingerprint"] != expected_production_fingerprint:
                _fail("PRODUCTION_FINGERPRINT_MISMATCH", "生产规则在批准后发生变化")
            counts = _change_counts(reconciliation["plan"])
            if counts != EXPECTED_CHANGE_COUNTS or reconciliation["extra_count"] or reconciliation["conflict_count"]:
                _fail("RULE_STATE_UNEXPECTED", "实时差异不再是批准的 7/4/14/3 计划")

            guarded_apply(
                reconciliation,
                expected_production_fingerprint=expected_production_fingerprint,
                expected_canonical_fingerprint=expected_canonical_fingerprint,
                actor=str(actor_user_id),
                reason=execution_reason,
                reconciliation_batch_id=release_commit,
                writer=lambda plan: _write_plan(
                    connection,
                    version_id=int(version["id"]),
                    actor_user_id=actor_user_id,
                    reason=execution_reason,
                    release_commit=release_commit,
                    plan=plan,
                    fault_injector=fault_injector,
                ),
            )

            final_rules = _read_rules(connection, int(version["id"]))
            final = reconcile_course_rules(
                version=version,
                production_rules=final_rules,
                policy=policy,
                reference_counts=references,
            )
            if not _is_already_applied(final):
                _fail("FINAL_VALIDATION_FAILED", "commit 前规则未达到 25 条逐字段 canonical")
            ledger_after = _verify_empty_ledger(connection)
            if ledger_after != ledger_before:
                _fail("PRECONDITION_CHANGED", "事务期间 ledger 状态发生变化")
            if get_settings().learning_credit_settlement_enabled:
                _fail("SETTLEMENT_ENABLED", "commit 前 settlement flag 已开启")
            if production_fingerprint(version, final_rules) == reconciliation["production_fingerprint"]:
                _fail("FINAL_VALIDATION_FAILED", "规则写入后 production fingerprint 未变化")
            # A zero-diff persisted projection has the canonical semantic
            # fingerprint, even though the pre-apply production fingerprint
            # also includes database identity and status fields.
            production_fingerprint_after = final["canonical_fingerprint"]
            if production_fingerprint_after != canonical_fp:
                _fail("FINAL_VALIDATION_FAILED", "最终语义指纹与 canonical 不一致")
            write_audit(
                connection,
                actor_user_id=actor_user_id,
                action="production.g5_4.course_rule_reconciliation.apply",
                resource_type="learning_plan_credit_rule_version",
                resource_id=str(version["id"]),
                purpose=execution_reason,
                before={"production_fingerprint": reconciliation["production_fingerprint"]},
                after={
                    "release_commit": release_commit,
                    "production_fingerprint_after": production_fingerprint_after,
                    "canonical_fingerprint": canonical_fp,
                    "keep_count": counts["KEEP"],
                    "update_count": counts["UPDATE"],
                    "add_count": counts["ADD"],
                    "remove_count": counts["REMOVE_UNUSED_PLACEHOLDER"],
                    "reason": execution_reason,
                },
                request_id=release_commit,
            )

            return {
                "operation": OPERATION_KEY,
                "status": "APPLIED",
                "release_commit": release_commit,
                "before": {
                    "rule_count": len(rules),
                    "fingerprint": reconciliation["production_fingerprint"],
                },
                "changes": {
                    "keep": counts["KEEP"],
                    "update": counts["UPDATE"],
                    "add": counts["ADD"],
                    "remove": counts["REMOVE_UNUSED_PLACEHOLDER"],
                },
                "after": {"rule_count": len(final_rules), "fingerprint": production_fingerprint_after},
                "bindings": bindings,
                "ledger_delta": 0,
                "settlement_enabled": False,
            }
    finally:
        if sqlite_lock_acquired:
            _sqlite_operation_lock.release()
