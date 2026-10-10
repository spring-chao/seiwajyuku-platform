"""Authenticated, fixed 0064-0067 preparation; no arbitrary SQL or auto-retry."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

from app.core.build_info import get_build_info
from app.core.settings import get_settings
from app.db import connect, execute
from app.migrations import MIGRATION_ROOT, _split_mysql
from app.services.audit import write_audit
from app.services import production_operations as rules
from app.services.course_credit_canonical import load_canonical_policy
from app.services.course_credit_reconciliation import reconcile_course_rules


MIGRATIONS = {
    "0064": ("0064_fix_credit_rule_mapping_and_binding_freeze.sql", "92cfe506dfbd9f1c776b910823e0135017c1af0df3a05472c85d52f3a91519e7"),
    "0065": ("0065_learning_credit_history_import.sql", "1fdfd3d58eee4f061feb63e98549a96995dc6878c77cb5afc9f5d4fb8d49c483"),
    "0066": ("0066_historical_credit_time_precision_review.sql", "0fcd889e127f97e84997957542673d281a2e2c0d725121ab3ed0a5fc279ae4e4"),
    "0067": ("0067_learning_credit_settlement_batches.sql", "ff33fd77a20c231694ba24d6ab065bc771f42e30596963315e34cd555130eee9"),
}
TABLES = {
    "0064": {"g5_4_c0_rule_mapping_state"},
    "0065": {"learning_credit_import_batches", "learning_credit_import_rows", "learning_credit_import_items"},
    "0066": {"learning_credit_import_class_mappings", "learning_credit_import_decisions"},
    "0067": {"learning_credit_settlement_batches", "learning_credit_settlement_batch_items"},
}
ACTION = "production.credit_settlement.setup"
FORMAT_ACTION = ACTION + ".0064_alias_format_repair"
UNFINISHED = "存在未完成的存储准备记录；不会自动重试，请核验实际结构"


def _fail(message, code="SETUP_NOT_READY", status_code=409):
    raise rules.ProductionOperationError(code, message, status_code=status_code)


def _rows(connection, sql, params=()):
    return [dict(row) for row in execute(connection, sql, params).fetchall()]


def _one(connection, sql, params=()):
    return dict(execute(connection, sql, params).fetchone())


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _alias_format_repairs(connection, version_id):
    """Only JSON whitespace/escaping; alias contents and their order must match."""
    expected = {r['course_key']: r['aliases'] for r in load_canonical_policy()['rules']}
    result = []
    for row in _rows(connection, "SELECT id,course_key,aliases_json FROM learning_plan_credit_rules WHERE rule_version_id=? ORDER BY course_key", (version_id,)):
        raw = row['aliases_json']
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if row['course_key'] not in expected or value != expected[row['course_key']]:
            return []
        canonical = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        if raw != canonical:
            result.append({'id': row['id'], 'course_key': row['course_key'], 'before': raw, 'after': canonical})
    return result


def _can_repair_alias_format(baseline, blockers):
    return (blockers == [UNFINISHED] and baseline['rule_status'] == 'DRAFT'
            and baseline['canonical_rules_ready'] and baseline['rule_apply_audit_count'] == 1
            and baseline['ledger']['total'] == 0 and baseline['mappings']['total'] == 0
            and baseline['bindings']['generic_frozen'] == baseline['bindings']['course_frozen'] == 0
            and all(not s['applied'] and not s['unrecorded_structure'] for s in baseline['stages'])
            and baseline['stages'][0]['attempted']
            and all(not s['attempted'] for s in baseline['stages'][1:])
            and baseline['format_repair_attempts'] == 0 and baseline['first_stage_attempts'] == 1
            and bool(baseline['alias_format_repairs']))


def _snapshot(connection):
    if isinstance(connection, sqlite3.Connection):
        _fail("固定结算存储准备仅适用于 MySQL；不使用 SQLite 结果替代生产验证")
    version = rules._read_version(connection, lock=False)
    reconciliation = reconcile_course_rules(
        version=version,
        production_rules=rules._read_rules(connection, version["id"], lock=False),
        policy=load_canonical_policy(),
        reference_counts=rules._reference_counts(connection, version["id"]),
    )
    names = tuple(name for name, _ in MIGRATIONS.values())
    markers = {row["version"] for row in _rows(connection, "SELECT version FROM schema_migrations WHERE version IN (?,?,?,?)", names)}
    all_tables = {row["TABLE_NAME"] for row in _rows(connection, "SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema=DATABASE()")}
    columns = {row["COLUMN_NAME"] for row in _rows(connection, "SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='learning_credit_entries'")}
    attempts = {row["resource_id"] for row in _rows(connection, "SELECT resource_id FROM audit_logs WHERE action=? AND result='STARTED'", (ACTION,))}
    generic_id = _one(connection, "SELECT id FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1'")["id"]
    bindings = _one(connection,
        "SELECT COUNT(*) AS total, COALESCE(SUM(b.credit_rule_version_id IS NOT NULL),0) AS generic_frozen, "
        "COALESCE(SUM(b.course_credit_rule_version_id IS NOT NULL),0) AS course_frozen, "
        "COALESCE(SUM((b.credit_rule_version_id IS NOT NULL AND b.credit_rule_version_id<>?) OR "
        "(b.course_credit_rule_version_id IS NOT NULL AND b.course_credit_rule_version_id<>?)),0) AS mismatched_frozen "
        "FROM class_learning_bindings b JOIN learning_plan_versions p ON p.id=b.plan_version_id "
        "WHERE p.plan_key='standard-3y' AND p.version_label='2026'", (generic_id, version["id"]))
    mappings = _one(connection, "SELECT COUNT(*) AS total, COALESCE(SUM(status='ACTIVE' AND generic_rule_version_id=? "
        "AND course_credit_rule_version_id=?),0) AS exact FROM learning_plan_credit_rule_mappings "
        "WHERE plan_key='standard-3y' AND plan_version_label='2026'", (generic_id, version["id"]))
    fill = _one(connection,
        "SELECT COUNT(*) AS total FROM study_meeting_courses c JOIN study_meeting_sessions s ON s.id=c.study_meeting_session_id "
        "JOIN class_learning_cycles lc ON lc.id=s.learning_cycle_id JOIN class_learning_bindings b ON b.id=lc.binding_id "
        "JOIN learning_plan_versions p ON p.id=b.plan_version_id WHERE p.plan_key='standard-3y' AND p.version_label='2026' AND c.credit_rule_version_id IS NULL")["total"]
    ledger = _one(connection, "SELECT COUNT(*) AS total, COALESCE(SUM(points),0) AS points FROM learning_credit_entries")
    audit_count = _one(connection, "SELECT COUNT(*) AS total FROM audit_logs WHERE action='production.g5_4.course_rule_reconciliation.apply' AND result='SUCCESS'")["total"]
    stages = []
    for key, (name, digest) in MIGRATIONS.items():
        present = sorted(TABLES[key] & all_tables)
        partial_period = key == "0066" and bool(columns & {"occurred_precision", "occurred_year", "occurred_month"})
        stages.append({"version": key, "filename": name, "sha256": digest, "applied": name in markers,
                       "schema_present": len(present) == len(TABLES[key]) and (key != "0066" or {"occurred_precision", "occurred_year", "occurred_month"} <= columns),
                       "unrecorded_structure": name not in markers and bool(present or partial_period), "attempted": name in attempts})
    baseline = {"stages": stages, "bindings": bindings, "mappings": mappings, "course_references_to_fill": fill, "ledger": ledger,
                "rule_status": version["status"], "canonical_rules_ready": rules._is_already_applied(reconciliation),
                "rule_fingerprint": reconciliation["production_fingerprint"], "rule_apply_audit_count": audit_count,
                "dependency_ready": "schema_migrations" in all_tables and bool(_rows(connection, "SELECT version FROM schema_migrations WHERE version='0063_complete_changzhou_wuxi_fee_and_service_address.sql'"))}
    baseline['alias_format_repairs'] = _alias_format_repairs(connection, version['id'])
    baseline['first_stage_attempts'] = _one(connection, "SELECT COUNT(*) AS n FROM audit_logs WHERE action=? AND resource_id=? AND result='STARTED'", (ACTION, MIGRATIONS['0064'][0]))['n']
    baseline['format_repair_attempts'] = _one(connection, "SELECT COUNT(*) AS n FROM audit_logs WHERE action=? AND result='STARTED'", (FORMAT_ACTION,))['n']
    blockers = []
    try:
        rules._verify_generic_rules(connection, lock=False)
    except rules.ProductionOperationError as error:
        blockers.append(error.message)
    if not baseline["canonical_rules_ready"] or audit_count != 1:
        blockers.append("请先完成已确认的 25 条课程规则收口")
    if ledger["total"] != 0:
        blockers.append("正式账本已有记录，首次存储准备需另行核对影响")
    if not baseline["dependency_ready"]:
        blockers.append("0063 基础迁移未登记")
    missing_seen = False
    for stage in stages:
        if not stage["applied"]:
            missing_seen = True
        elif missing_seen or not stage["schema_present"]:
            blockers.append("迁移登记与实际结构不一致，请核验后做前向修复")
        if not stage["applied"] and (stage["attempted"] or stage["unrecorded_structure"]):
            blockers.append(UNFINISHED)
    if stages[0]["applied"]:
        if version["status"] != "PUBLISHED" or bindings["total"] != bindings["generic_frozen"] or bindings["total"] != bindings["course_frozen"] or bindings["mismatched_frozen"] or mappings["total"] != 1 or mappings["exact"] != 1 or fill:
            blockers.append("规则发布、绑定冻结或课程引用未完整完成")
    elif version["status"] != "DRAFT" or bindings["generic_frozen"] or bindings["course_frozen"]:
        blockers.append("首次规则冻结基线不符合已确认范围")
    return baseline, blockers


def preview(actor_user_id: int):
    connection = connect()
    try:
        rules._verify_actor(connection, actor_user_id)
        baseline, blockers = _snapshot(connection)
        settings = get_settings()
        setup_enabled = settings.credit_settlement_setup_enabled
        next_stage = next((s for s in baseline["stages"] if not s["applied"]), None)
        storage_ready = not blockers and next_stage is None
        gates = {"settlement": settings.learning_credit_settlement_enabled,
                 "dry_run": settings.learning_credit_batch_dry_run_enabled,
                 "approval": settings.learning_credit_batch_approval_enabled,
                 "post": settings.learning_credit_batch_post_enabled}
        if next_stage and not setup_enabled:
            blockers.append("固定结算存储准备开关尚未开启")
        if next_stage and (settings.learning_credit_settlement_enabled or settings.run_bootstrap_on_startup or settings.deployment_read_only or not settings.allow_production_mutations):
            blockers.append("存储准备需要结算关闭、启动迁移关闭及授权写入环境")
        return {"release_commit": get_build_info()["commit_sha"], "baseline_fingerprint": _hash(baseline),
                **baseline, "ledger": {"total": baseline["ledger"]["total"], "points": str(baseline["ledger"]["points"])},
                "blockers": blockers, "next_migration": next_stage["version"] if next_stage else None,
                "can_prepare": bool(next_stage) and not blockers, "storage_ready": storage_ready,
                "can_repair_alias_format": setup_enabled and _can_repair_alias_format(baseline, blockers),
                "settlement_gates": gates, "formal_ready": storage_ready and all(gates.values())}
    finally:
        connection.rollback()
        connection.close()


def prepare(*, actor_user_id: int, migration_version: str, expected_release_commit: str,
            expected_baseline_fingerprint: str, expected_migration_sha256: str, execution_reason: str,
            repair_alias_format: bool = False):
    if repair_alias_format and migration_version != '0064':
        _fail("格式修复仅允许尚未创建持久结构的 0064")
    if migration_version not in MIGRATIONS:
        _fail("不是允许的固定结算迁移")
    settings = get_settings()
    if not settings.credit_settlement_setup_enabled or settings.learning_credit_settlement_enabled or settings.run_bootstrap_on_startup:
        _fail("当前环境未开放固定结算存储准备")
    if settings.deployment_read_only or not settings.allow_production_mutations:
        _fail("当前环境不允许生产写入", status_code=403)
    if len(execution_reason.strip()) < 8 or len(execution_reason) > 1000:
        _fail("请填写完整执行原因")
    commit = get_build_info()["commit_sha"]
    if len(commit) != 40 or commit != expected_release_commit:
        _fail("服务版本发生变化，请刷新核验")
    name, digest = MIGRATIONS[migration_version]
    content = (MIGRATION_ROOT / "mysql" / name).read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(content).hexdigest() != digest or digest != expected_migration_sha256:
        _fail("固定迁移内容校验失败")
    connection = connect()
    reserved = False
    locked = False
    statement_index = None
    try:
        rules._verify_actor(connection, actor_user_id)
        if isinstance(connection, sqlite3.Connection):
            _fail("该固定操作仅允许 MySQL")
        locked = _one(connection, "SELECT GET_LOCK(?,0) AS acquired", (rules.OPERATION_LOCK_NAME,))["acquired"] == 1
        if not locked:
            _fail("另一个规则或迁移操作正在执行")
        baseline, blockers = _snapshot(connection)
        if repair_alias_format and not _can_repair_alias_format(baseline, blockers):
            _fail("实际结构或规则不符合固定别名格式修复条件；不重跑已完成或部分执行的迁移")
        if blockers and not repair_alias_format:
            _fail("；".join(blockers))
        next_stage = next((s for s in baseline["stages"] if not s["applied"]), None)
        if not next_stage or next_stage["version"] != migration_version or _hash(baseline) != expected_baseline_fingerprint:
            _fail("迁移顺序或实时基线已变化，请刷新核验；已登记迁移不会重跑")
        operation_action = FORMAT_ACTION if repair_alias_format else ACTION
        write_audit(connection, actor_user_id=actor_user_id, action=operation_action, resource_type="schema_migration", resource_id=name,
                    purpose=execution_reason.strip(), result="STARTED", after={"commit": commit, "sha256": digest,
                    "baseline_fingerprint": expected_baseline_fingerprint, "bindings": baseline["bindings"],
                    "course_references_to_fill": baseline["course_references_to_fill"],
                    "alias_format_repairs": baseline['alias_format_repairs'] if repair_alias_format else []})
        connection.commit()
        reserved = True
        if repair_alias_format:
            for row in baseline['alias_format_repairs']:
                cursor = execute(connection, "UPDATE learning_plan_credit_rules SET aliases_json=? WHERE id=? AND aliases_json=?", (row['after'], row['id'], row['before']))
                if cursor.rowcount != 1:
                    _fail("课程别名格式发生变化，需要核验实际状态")
            write_audit(connection, actor_user_id=actor_user_id, action=FORMAT_ACTION + '.normalize', resource_type='course_credit_rules', resource_id=name, purpose=execution_reason.strip(), before=baseline['alias_format_repairs'], after={'semantic_change': False})
            connection.commit()
        for statement_index, statement in enumerate(_split_mysql(content.decode("utf-8")), start=1):
            execute(connection, statement)
        after_ledger = _one(connection, "SELECT COUNT(*) AS total, COALESCE(SUM(points),0) AS points FROM learning_credit_entries")
        if after_ledger != baseline["ledger"]:
            _fail("账本不变量发生变化，需要核验实际结果")
        # A missing marker after partial DDL cannot be replayed: STARTED survives.
        execute(connection, "INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)", (name, datetime.now(UTC).replace(tzinfo=None)))
        after, after_blockers = _snapshot(connection)
        if after_blockers:
            _fail("迁移后不变量核验未通过，请核验实际结果")
        write_audit(connection, actor_user_id=actor_user_id, action=operation_action + ".complete", resource_type="schema_migration", resource_id=name,
                    purpose=execution_reason.strip(), after={"commit": commit, "sha256": digest, "ledger_delta": 0,
                    "bindings": after["bindings"], "course_references_to_fill": after["course_references_to_fill"]})
        connection.commit()
        return {"status": "RECORDED", "migration_version": migration_version, "ledger_delta": 0}
    except Exception as error:
        connection.rollback()
        if reserved:
            try:
                numeric_code = error.args[0] if error.args and isinstance(error.args[0], int) else None
                write_audit(connection, actor_user_id=actor_user_id, action=operation_action + '.failure', resource_type='schema_migration', resource_id=name, result='FAILED', after={'statement_index': statement_index, 'error_type': type(error).__name__, 'mysql_error_code': numeric_code})
                connection.commit()
            except Exception:
                connection.rollback()
            _fail("执行结果需要核验；已保留开始记录，不会自动重试或回退", code="SETUP_OUTCOME_UNKNOWN")
        if isinstance(error, rules.ProductionOperationError):
            raise
        _fail("固定存储准备核验失败；未开始执行", code="SETUP_PREFLIGHT_FAILED")
    finally:
        if locked:
            try:
                execute(connection, "SELECT RELEASE_LOCK(?)", (rules.OPERATION_LOCK_NAME,))
            except Exception:
                pass  # Closing the dedicated connection also releases its lock.
        connection.close()
