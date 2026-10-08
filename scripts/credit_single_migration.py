"""One independently approved, fixed MySQL credit migration; no migrate-to-latest.

Plan is offline. Execution requires live signed cloud/DB evidence, a separate
approval, exact source hashes, TLS, and a private durable one-attempt journal.
DDL may commit implicitly: unknown outcomes are never replayed or auto-downed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from r3_authorization import read_document, verify_authorization
from r3_cloud_read import GATE_KEYS, SCOPE, SERVICE, require_stable_flow
from r3_collect_db_readonly import BASE_FIELDS, verify_checkout
from r3_collect_readonly import _load_verifier_key
from r3_db_read import MysqlSelectPort, QueryId, derive_baseline
from r3_read_evidence import ReadFailure, fingerprint, verify_bundle


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "platform-api"))
MIGRATIONS = {
    "0064": ("0064_fix_credit_rule_mapping_and_binding_freeze.sql", "e915256233248a7759014cd5191dc575e0890204cfa3ec420dacdaeaedeab4dd"),
    "0065": ("0065_learning_credit_history_import.sql", "1fdfd3d58eee4f061feb63e98549a96995dc6878c77cb5afc9f5d4fb8d49c483"),
    "0066": ("0066_historical_credit_time_precision_review.sql", "0fcd889e127f97e84997957542673d281a2e2c0d725121ab3ed0a5fc279ae4e4"),
    "0067": ("0067_learning_credit_settlement_batches.sql", "ff33fd77a20c231694ba24d6ab065bc771f42e30596963315e34cd555130eee9"),
}
FIELDS = (BASE_FIELDS - {"query_fingerprint", "max_batches", "max_writes"}) | {
    "migration_version", "migration_sha256", "migration_statement_count", "max_runs",
    "cloud_bundle_fingerprint", "db_bundle_fingerprint", "runtime_commit",
    "backup_ref", "backup_evidence_sha256", "release_manifest_fingerprint",
    "expected_policy_binding_count", "expected_course_reference_fill_count",
}
BUSINESS_QUERIES = tuple(q for q in QueryId if q not in (QueryId.ACCESS, QueryId.PRIVILEGES))
ACCESS = "SELECT DATABASE() AS database_name,CURRENT_USER() AS principal,CURRENT_ROLE() AS roles"
DEPENDENCY = "SELECT version FROM schema_migrations WHERE version='0063_complete_changzhou_wuxi_fee_and_service_address.sql'"
LOCK = "SELECT GET_LOCK(%s,0) AS acquired"
STAMP = "INSERT INTO schema_migrations(version,applied_at) VALUES (%s,UTC_TIMESTAMP())"
POLICY_SCOPE = "SELECT COUNT(*) AS count,CAST(COALESCE(SUM(b.credit_rule_version_id IS NOT NULL),0) AS UNSIGNED) AS generic_frozen,CAST(COALESCE(SUM(b.course_credit_rule_version_id IS NOT NULL),0) AS UNSIGNED) AS course_frozen FROM class_learning_bindings b JOIN learning_plan_versions p ON p.id=b.plan_version_id WHERE p.plan_key='standard-3y' AND p.version_label='2026'"
COURSE_FILL = "SELECT COUNT(*) AS count FROM study_meeting_courses c JOIN study_meeting_sessions s ON s.id=c.study_meeting_session_id JOIN class_learning_cycles lc ON lc.id=s.learning_cycle_id JOIN class_learning_bindings b ON b.id=lc.binding_id JOIN learning_plan_versions p ON p.id=b.plan_version_id WHERE p.plan_key='standard-3y' AND p.version_label='2026' AND c.credit_rule_version_id IS NULL"


def plan(version):
    from app.migrations import _split_mysql

    if version not in MIGRATIONS:
        raise ReadFailure("MIGRATION_NOT_ALLOWLISTED")
    name, expected = MIGRATIONS[version]
    raw = (ROOT / "migrations" / "mysql" / name).read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ReadFailure("MIGRATION_SOURCE_CHANGED")
    statements = _split_mysql(raw.decode("utf-8"))
    return {"version": version, "filename": name, "sha256": expected,
            "statement_count": len(statements), "production_ready": False}, statements


def validate_grant(document, *, key, commit, now, migration_plan):
    grant = verify_authorization(document, key=key, purpose="CREDIT_SINGLE_MYSQL_MIGRATION",
                                 scope=SCOPE, commit=commit, now=now)
    if (
        set(grant) != FIELDS
        or any(not isinstance(grant[k], str) for k in (
            "database_name", "host", "principal", "baseline_revision", "runtime_commit", "backup_ref"))
        or grant["migration_version"] != migration_plan["version"]
        or grant["migration_sha256"] != migration_plan["sha256"]
        or type(grant["migration_statement_count"]) is not int
        or grant["migration_statement_count"] != migration_plan["statement_count"]
        or type(grant["max_runs"]) is not int or grant["max_runs"] != 1
        or any(type(grant[k]) is not int or grant[k] < 0 for k in (
            "expected_policy_binding_count", "expected_course_reference_fill_count"))
        or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", str(grant["database_name"]))
        or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", str(grant["host"]))
        or type(grant["port"]) is not int or not 1 <= grant["port"] <= 65535
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}@[A-Za-z0-9_.%:-]{1,253}", str(grant["principal"]))
        or not re.fullmatch(re.escape(SERVICE) + r"-[0-9]+", str(grant["baseline_revision"]))
        or not re.fullmatch(r"[a-f0-9]{40}", str(grant["runtime_commit"]))
        or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,160}", str(grant["backup_ref"]))
        or any(not re.fullmatch(r"[a-f0-9]{64}", str(grant[k])) for k in (
            "journal_fingerprint", "cloud_bundle_fingerprint", "db_bundle_fingerprint",
            "backup_evidence_sha256", "release_manifest_fingerprint"))
    ):
        raise ReadFailure("MIGRATION_AUTHORIZATION_SCOPE_INVALID")
    return grant


class MigrationJournal:
    """Spend before opening the DB. Preserve records even on precheck refusal."""

    def __init__(self, path):
        self.path = Path(path).absolute()
        if self.path.is_symlink():
            raise ReadFailure("MIGRATION_JOURNAL_LINK_DISALLOWED")
        try:
            existed = self.path.exists()
            with self._db() as connection:
                if existed:
                    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if connection.execute("PRAGMA user_version").fetchone()[0] != 1 or tables != {"migration_attempts"}:
                        raise ReadFailure("MIGRATION_JOURNAL_CORRUPT")
                else:
                    connection.execute("PRAGMA user_version=1")
                    connection.execute("CREATE TABLE migration_attempts(approval_ref TEXT PRIMARY KEY,"
                        "target_fingerprint TEXT NOT NULL,migration_version TEXT NOT NULL,grant_fingerprint TEXT NOT NULL,"
                        "started_at REAL NOT NULL,outcome TEXT NOT NULL,UNIQUE(target_fingerprint,migration_version))")
        except sqlite3.Error:
            raise ReadFailure("MIGRATION_JOURNAL_UNAVAILABLE") from None

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path, timeout=2)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ReadFailure("MIGRATION_JOURNAL_CORRUPT")
            with connection:
                yield connection
        finally:
            connection.close()

    def reserve(self, grant, now):
        if grant["journal_fingerprint"] != fingerprint(self.path.resolve().as_posix()):
            raise ReadFailure("MIGRATION_JOURNAL_TARGET_MISMATCH")
        target = fingerprint({k: grant[k] for k in ("scope", "host", "port", "database_name")})
        try:
            with self._db() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("INSERT INTO migration_attempts VALUES (?,?,?,?,?,'UNKNOWN')",
                    (grant["approval_ref"], target, grant["migration_version"], fingerprint(grant), now))
        except sqlite3.IntegrityError:
            raise ReadFailure("MIGRATION_REPLAY_DISALLOWED") from None
        except sqlite3.Error:
            raise ReadFailure("MIGRATION_JOURNAL_UNAVAILABLE") from None

    def recorded(self, approval_ref):
        try:
            with self._db() as connection:
                changed = connection.execute("UPDATE migration_attempts SET outcome='RECORDED' WHERE approval_ref=? AND outcome='UNKNOWN'", (approval_ref,)).rowcount
                if changed != 1:
                    raise ReadFailure("MIGRATION_JOURNAL_CORRUPT")
        except sqlite3.Error:
            raise ReadFailure("MIGRATION_JOURNAL_UNAVAILABLE") from None


def verified_baseline(grant, *, cloud_bundle, db_bundle, collector_key, now):
    if (cloud_bundle.get("bundle_fingerprint") != grant["cloud_bundle_fingerprint"]
        or db_bundle.get("bundle_fingerprint") != grant["db_bundle_fingerprint"]):
        raise ReadFailure("MIGRATION_EVIDENCE_BINDING_MISMATCH")
    kwargs = dict(key=collector_key, expected_scope=SCOPE,
                  expected_revision=grant["baseline_revision"], now=now)
    cloud = verify_bundle(cloud_bundle, **kwargs)
    records = verify_bundle(db_bundle, **kwargs)
    kinds = {k: [r for r in cloud if r.kind == k] for k in (
        "ServiceEvidence", "ReleaseEvidence", "RevisionEvidence", "ManageTaskEvidence", "RuntimeEvidence")}
    if any(len(kinds[k]) != 2 for k in ("ServiceEvidence", "ReleaseEvidence", "RevisionEvidence", "ManageTaskEvidence")):
        raise ReadFailure("MIGRATION_CLOUD_EVIDENCE_INCOMPLETE")
    if any(kinds[k][0].payload() != kinds[k][1].payload() for k in (
        "ServiceEvidence", "ReleaseEvidence", "RevisionEvidence", "ManageTaskEvidence")):
        raise ReadFailure("MIGRATION_CLOUD_BASELINE_CHANGED")
    for service, release in zip(kinds["ServiceEvidence"], kinds["ReleaseEvidence"]):
        require_stable_flow(service, release, grant["baseline_revision"])
    for revision in kinds["RevisionEvidence"]:
        p = revision.payload()
        if p.get("revision") != grant["baseline_revision"] or p.get("status") != "normal" or p.get("gates") != dict.fromkeys(GATE_KEYS, "false"):
            raise ReadFailure("MIGRATION_RUNTIME_GATES_NOT_CLOSED")
    if any(r.payload().get("task_inactive") is not True for r in kinds["ManageTaskEvidence"]):
        raise ReadFailure("MIGRATION_MANAGE_TASK_ACTIVE")
    builds = [r for r in kinds["RuntimeEvidence"] if r.source == "runtime-http:/api/v1/system/build-info"]
    if (len(kinds["RuntimeEvidence"]) != 3 or {r.source for r in kinds["RuntimeEvidence"]} != {
        "runtime-http:/api/v1/system/build-info", "runtime-http:/health/live", "runtime-http:/api/v1/health"}
        or any(r.payload().get("http_status") != 200 for r in kinds["RuntimeEvidence"])
        or len(builds) != 1 or builds[0].payload().get("commit_sha") != grant["runtime_commit"]
        or builds[0].payload().get("environment") != "production"):
        raise ReadFailure("MIGRATION_RUNTIME_UNVERIFIED")
    if len(records) != 1 or records[0].kind != "DatabaseBaselineEvidence":
        raise ReadFailure("MIGRATION_DATABASE_EVIDENCE_INCOMPLETE")
    p = records[0].payload()
    if p.get("connection_target_fingerprint") != fingerprint({"scope": SCOPE, "database": grant["database_name"]}):
        raise ReadFailure("MIGRATION_DATABASE_TARGET_MISMATCH")
    return p


def snapshot(connection):
    port = MysqlSelectPort(connection)
    rows = {q: port.read(q) for q in BUSINESS_QUERIES}
    return rows, derive_baseline(rows)


def preconditions(version, rows, before):
    from app.services.course_credit_canonical import canonical_rule_rows, expected_persisted_rule

    if any(before["migration_status"][n] != ("APPLIED" if n < version else "NOT_APPLIED") for n in MIGRATIONS):
        raise ReadFailure("MIGRATION_ORDER_OR_ALREADY_APPLIED")
    expected = {r["course_key"]: expected_persisted_rule(r) for r in canonical_rule_rows()}
    actual = {}
    for r in rows[QueryId.RULES]:
        actual[r["course_key"]] = {k: r[k] for k in ("course_key", "course_name", "year_index", "credit_points", "status", "source")}
        actual[r["course_key"]]["aliases"] = json.loads(r["aliases_json"])
    if actual != expected or len(rows[QueryId.RULES]) != 25:
        raise ReadFailure("MIGRATION_CANONICAL_RULES_MISMATCH")
    if before["generic_rule_version_count"] != 1 or before["generic_rule_statuses"] != ["PUBLISHED"] or before["generic_active_rule_count"] != 6:
        raise ReadFailure("MIGRATION_GENERIC_RULES_UNVERIFIED")
    if before["course_rule_status"] != ("DRAFT" if version == "0064" else "PUBLISHED") or before["apply_audit_count"] != 1:
        raise ReadFailure("MIGRATION_RULE_APPLY_NOT_VERIFIED")
    expected_frozen = 0 if version == "0064" else before["active_binding_count"]
    if before["mapping_count"] != (0 if version == "0064" else 1) or before["generic_frozen_count"] != expected_frozen or before["course_frozen_count"] != expected_frozen:
        raise ReadFailure("MIGRATION_MAPPING_OR_BINDING_UNVERIFIED")


def execute_one(document, *, authorization_key, collector_key, commit, version,
                cloud_bundle, db_bundle, release_manifest, backup_evidence,
                journal, connect, clock=time.time):
    from build_provenance import validate_manifest

    migration_plan, statements = plan(version)
    grant = validate_grant(document, key=authorization_key, commit=commit, now=clock(), migration_plan=migration_plan)
    if authorization_key == collector_key or not isinstance(journal, MigrationJournal):
        raise ReadFailure("MIGRATION_INDEPENDENT_APPROVAL_AND_JOURNAL_REQUIRED")
    verified = verified_baseline(grant, cloud_bundle=cloud_bundle, db_bundle=db_bundle,
                                collector_key=collector_key, now=clock())
    manifest = validate_manifest(Path(release_manifest))
    if manifest["release_commit"] != commit or fingerprint(manifest) != grant["release_manifest_fingerprint"]:
        raise ReadFailure("MIGRATION_RELEASE_PROVENANCE_MISMATCH")
    if hashlib.sha256(Path(backup_evidence).read_bytes()).hexdigest() != grant["backup_evidence_sha256"]:
        raise ReadFailure("MIGRATION_BACKUP_EVIDENCE_MISMATCH")
    journal.reserve(grant, clock())
    connection = None
    write_started = False
    try:
        connection = connect(grant)
        with connection.cursor() as cursor:
            cursor.execute(ACCESS)
            identity = cursor.fetchone()
            if identity != {"database_name": grant["database_name"], "principal": grant["principal"], "roles": "NONE"}:
                raise ReadFailure("MIGRATION_DATABASE_IDENTITY_MISMATCH")
            cursor.execute(LOCK, ("credit:" + fingerprint({"database": grant["database_name"]})[:56],))
            if cursor.fetchone() != {"acquired": 1}:
                raise ReadFailure("MIGRATION_DATABASE_LOCK_UNAVAILABLE")
            cursor.execute(DEPENDENCY)
            if cursor.fetchone() != {"version": "0063_complete_changzhou_wuxi_fee_and_service_address.sql"}:
                raise ReadFailure("MIGRATION_BASE_SCHEMA_UNVERIFIED")
        rows, before = snapshot(connection)
        if verified.get("query_results") != {q.value: fingerprint(rows[q]) for q in BUSINESS_QUERIES}:
            raise ReadFailure("MIGRATION_DATABASE_BASELINE_CHANGED")
        preconditions(version, rows, before)
        with connection.cursor() as cursor:
            cursor.execute(POLICY_SCOPE)
            scope = cursor.fetchone()
            expected_frozen = 0 if version == "0064" else grant["expected_policy_binding_count"]
            if scope != {"count": grant["expected_policy_binding_count"], "generic_frozen": expected_frozen, "course_frozen": expected_frozen}:
                raise ReadFailure("MIGRATION_FULL_BINDING_SCOPE_CHANGED")
            cursor.execute(COURSE_FILL)
            if cursor.fetchone() != {"count": grant["expected_course_reference_fill_count"]} or (version != "0064" and grant["expected_course_reference_fill_count"] != 0):
                raise ReadFailure("MIGRATION_COURSE_REFERENCE_SCOPE_CHANGED")
        # Recheck freshness after connection/SELECTs, immediately before writes.
        verified_baseline(grant, cloud_bundle=cloud_bundle, db_bundle=db_bundle,
                          collector_key=collector_key, now=clock())
        with connection.cursor() as cursor:
            for statement in statements:
                if not grant["not_before"] <= clock() < grant["expires_at"]:
                    raise ReadFailure("AUTHORIZATION_EXPIRED_OR_FUTURE")
                write_started = True
                cursor.execute(statement)
            after_rows, after = snapshot(connection)
            if rows[QueryId.RULES] != after_rows[QueryId.RULES] or rows[QueryId.GENERIC_VERSION] != after_rows[QueryId.GENERIC_VERSION]:
                raise ReadFailure("MIGRATION_RULE_CONTENT_CHANGED")
            cursor.execute(POLICY_SCOPE)
            if cursor.fetchone() != dict.fromkeys(("count", "generic_frozen", "course_frozen"), grant["expected_policy_binding_count"]):
                raise ReadFailure("MIGRATION_FULL_BINDING_FREEZE_NOT_PROVEN")
            cursor.execute(COURSE_FILL)
            if cursor.fetchone() != {"count": 0}:
                raise ReadFailure("MIGRATION_COURSE_REFERENCES_NOT_FILLED")
            unchanged = ("ledger_entry_count", "ledger_points", "apply_audit_count", "generic_rules_fingerprint", "active_binding_count")
            if any(before[k] != after[k] for k in unchanged):
                raise ReadFailure("MIGRATION_POST_INVARIANT_CHANGED")
            if version == "0064":
                if after["course_rule_status"] != "PUBLISHED" or after["mapping_count"] != 1 or any(after[k] != before["active_binding_count"] for k in ("generic_frozen_count", "course_frozen_count")):
                    raise ReadFailure("MIGRATION_FREEZE_NOT_PROVEN")
            elif any(before[k] != after[k] for k in ("production_fingerprint", "mapping_count", "generic_frozen_count", "course_frozen_count")):
                raise ReadFailure("MIGRATION_RULES_OR_BINDINGS_CHANGED")
            if not grant["not_before"] <= clock() < grant["expires_at"]:
                raise ReadFailure("AUTHORIZATION_EXPIRED_OR_FUTURE")
            cursor.execute(STAMP, (migration_plan["filename"],))
        connection.commit()
        journal.recorded(grant["approval_ref"])
        return {"result": "MIGRATION_RECORDED", "migration_version": version,
                "migration_sha256": migration_plan["sha256"], "approval_ref": grant["approval_ref"],
                "controller_commit": commit, "attempts": 1, "replay_allowed": False,
                "ledger_entry_count": after["ledger_entry_count"], "ledger_points": after["ledger_points"],
                "production_ready": False}
    except Exception:
        # A failed commit or DDL timeout is unknown; rollback cannot prove undo.
        raise ReadFailure("MIGRATION_OUTCOME_UNKNOWN" if write_started else "MIGRATION_PRECHECK_REFUSED") from None
    finally:
        if connection is not None:
            try:
                connection.close()  # session advisory lock is released here
            except Exception:
                pass


def connect_mysql(grant):
    import pymysql

    password, ca = os.getenv("CREDIT_MIGRATION_PASSWORD"), os.getenv("CREDIT_MIGRATION_TLS_CA")
    if not password or not ca or not Path(ca).is_file():
        raise ReadFailure("MIGRATION_CREDENTIALS_OR_TLS_NOT_CONFIGURED")
    return pymysql.connect(host=grant["host"], port=grant["port"],
        user=grant["principal"].split("@", 1)[0], password=password,
        database=grant["database_name"], ssl_ca=ca, ssl_verify_cert=True, ssl_verify_identity=True,
        autocommit=None, connect_timeout=5, read_timeout=30, write_timeout=5,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--migration", choices=tuple(MIGRATIONS), required=True)
    for flag in ("authorization", "authorization-key-file", "verifier-key-file", "cloud-evidence",
                 "db-evidence", "release-manifest", "backup-evidence", "journal", "output"):
        parser.add_argument("--" + flag, type=Path)
    parser.add_argument("--controller-commit")
    args = parser.parse_args(argv)
    receipt = None
    try:
        if args.plan:
            print(json.dumps(plan(args.migration)[0], ensure_ascii=False))
            return 0
        if any(getattr(args, name) is None for name in (
            "authorization", "authorization_key_file", "verifier_key_file", "cloud_evidence", "db_evidence",
            "release_manifest", "backup_evidence", "journal", "output", "controller_commit")):
            raise ReadFailure("MIGRATION_EXECUTION_MATERIAL_REQUIRED")
        if args.output.exists():
            raise ReadFailure("MIGRATION_OUTPUT_ALREADY_EXISTS")
        auth_key = _load_verifier_key(args.authorization_key_file, args.output)
        _load_verifier_key(args.authorization_key_file, args.authorization)
        evidence_key = _load_verifier_key(args.verifier_key_file, args.output)
        if args.journal.parent.resolve() != args.authorization_key_file.parent.resolve() or args.journal.resolve() in {args.authorization_key_file.resolve(), args.verifier_key_file.resolve()}:
            raise ReadFailure("MIGRATION_PRIVATE_JOURNAL_REQUIRED")
        verify_checkout(args.controller_commit)
        receipt = execute_one(read_document(args.authorization), authorization_key=auth_key,
            collector_key=evidence_key, commit=args.controller_commit, version=args.migration,
            cloud_bundle=read_document(args.cloud_evidence), db_bundle=read_document(args.db_evidence),
            release_manifest=args.release_manifest, backup_evidence=args.backup_evidence,
            journal=MigrationJournal(args.journal), connect=connect_mysql)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
        print(json.dumps(receipt))
        return 0
    except ReadFailure as exc:
        print(json.dumps({"result": "STOPPED", "code": exc.code, "production_ready": False, "replay_allowed": False}))
    except Exception:
        code = "MIGRATION_RECEIPT_WRITE_FAILED" if receipt is not None else "MIGRATION_MATERIAL_OR_OUTPUT_FAILED"
        print(json.dumps({"result": "STOPPED", "code": code, "production_ready": False, "replay_allowed": False}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
