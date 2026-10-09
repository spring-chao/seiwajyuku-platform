"""Explicitly approved R3 SELECT-only production baseline collector.

No writer, migrations, free SQL, application DATABASE_URL, automatic login,
account provisioning, read-only/role reconfiguration, reconnect or retry. The old Phase 3
DatabaseReadAdapter.collect() stays disabled; this is a separate reviewed entry.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from r3_authorization import ReadAttemptJournal, read_document, verify_authorization
from r3_cloud_read import SCOPE, SERVICE
from r3_collect_readonly import _load_verifier_key
from r3_db_read import DatabaseReadAdapter, MysqlSelectPort, QueryId, SQL
from r3_read_evidence import ReadFailure, _Issuer, fingerprint, verify_bundle


BASE_FIELDS = frozenset({
    "schema_version", "purpose", "scope", "controller_commit", "approval_ref",
    "approved_by", "realm", "not_before", "expires_at", "database_name",
    "host", "port", "principal", "baseline_revision", "query_fingerprint", "journal_fingerprint", "max_batches", "max_writes",
})

APPROVED_COLUMNS = {
    "learning_plan_credit_rule_versions": {"id", "plan_key", "version_label", "status", "based_on_version_label"},
    "learning_plan_credit_rules": {"id", "rule_version_id", "course_key", "course_name", "year_index", "credit_points", "status", "source", "aliases_json"},
    "learning_credit_rule_versions": {"id", "rule_set_key", "version_label", "status"},
    "learning_credit_rules": {"rule_version_id", "rule_key", "settlement_model", "points", "cap_points", "status"},
    "learning_plan_credit_rule_mappings": {"plan_key", "plan_version_label", "generic_rule_version_id", "course_credit_rule_version_id"},
    "class_learning_bindings": {"status", "plan_version_id", "credit_rule_version_id", "course_credit_rule_version_id"},
    "learning_plan_versions": {"id", "plan_key", "version_label"},
    "learning_credit_entries": {"points", "rule_key"},
    "schema_migrations": {"version"},
    "audit_logs": {"action"},
    "study_meeting_sessions": {"course_key"},
    "study_meeting_courses": {"id", "course_key"},
    "study_meeting_course_completions": {"study_meeting_course_id"},
}


def validate_privileges(rows, database):
    """SELECT-only is insufficient: reject global/schema/table-wide reads too."""
    if not rows:
        raise ReadFailure("DB_PRIVILEGE_SCOPE_UNVERIFIED")
    for row in rows:
        if row.get("privilege_type") == "USAGE" and row.get("grant_scope") == "GLOBAL" and row.get("is_grantable") == "NO":
            continue
        if (
            row.get("privilege_type") != "SELECT"
            or row.get("grant_scope") != "COLUMN"
            or row.get("database_name") != database
            or row.get("column_name") not in APPROVED_COLUMNS.get(row.get("table_name"), set())
            or row.get("is_grantable") != "NO"
        ):
            raise ReadFailure("DB_PRIVILEGE_SCOPE_UNVERIFIED")


def query_fingerprint():
    return fingerprint({q.value: sql for q, sql in SQL.items()})


def validate_grant(document, *, key, commit, now):
    grant = verify_authorization(
        document, key=key, purpose="R3_DB_BASELINE_READ", scope=SCOPE,
        commit=commit, now=now,
    )
    if (
        set(grant) != BASE_FIELDS
        or grant["query_fingerprint"] != query_fingerprint()
        or not isinstance(grant["journal_fingerprint"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", grant["journal_fingerprint"])
        or type(grant["max_batches"]) is not int or grant["max_batches"] != 1
        or type(grant["max_writes"]) is not int or grant["max_writes"] != 0
        or not isinstance(grant["database_name"], str)
        or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", grant["database_name"])
        or not isinstance(grant["host"], str)
        or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", grant["host"])
        or type(grant["port"]) is not int or not 1 <= grant["port"] <= 65535
        or not isinstance(grant["baseline_revision"], str)
        or not re.fullmatch(re.escape(SERVICE) + r"-[0-9]+", grant["baseline_revision"])
        or not isinstance(grant["principal"], str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}@[A-Za-z0-9_.%:-]{1,253}", grant["principal"])
    ):
        raise ReadFailure("DB_AUTHORIZATION_SCOPE_INVALID")
    return grant


class _ApprovedPort:
    def __init__(self, connection, grant, clock):
        self.__port = MysqlSelectPort(connection)
        self.__grant, self.__clock = grant, clock

    def read(self, query_id):
        if not self.__grant["not_before"] <= self.__clock() < self.__grant["expires_at"]:
            raise ReadFailure("AUTHORIZATION_EXPIRED_OR_FUTURE")
        rows = self.__port.read(query_id)
        if query_id is QueryId.ACCESS and (
            len(rows) != 1 or rows[0].get("principal") != self.__grant["principal"]
        ):
            raise ReadFailure("DB_PRINCIPAL_MISMATCH")
        if query_id is QueryId.PRIVILEGES:
            validate_privileges(rows, self.__grant["database_name"])
        return rows


def collect_approved_baseline(
    document, *, authorization_key, issuer, commit, journal,
    connect, revision, clock=time.time,
):
    grant = validate_grant(document, key=authorization_key, commit=commit, now=clock())
    if issuer.realm != "live" or revision != grant["baseline_revision"]:
        raise ReadFailure("DB_COLLECTOR_IDENTITY_INVALID")
    if not isinstance(journal, ReadAttemptJournal):
        raise ReadFailure("READ_JOURNAL_REQUIRED")
    journal.reserve(grant, clock())  # committed before any connection attempt
    connection = None
    try:
        connection = connect(grant)
        port = _ApprovedPort(connection, grant, clock)
        started = issuer.clock()
        record = DatabaseReadAdapter(
            issuer, port, scope=SCOPE, database_name=grant["database_name"],
        )._collect()
        if not grant["not_before"] <= clock() < grant["expires_at"]:
            raise ReadFailure("AUTHORIZATION_EXPIRED_OR_FUTURE")
        result = issuer.bundle(SCOPE, revision, [record], started)
        # The bundle remains production_ready=false: it is one evidence class.
        return result
    except ReadFailure:
        raise
    except Exception:
        raise ReadFailure("DB_READ_FAILED") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass  # never mask an outcome or expose driver exceptions


def connect_readonly(grant):
    import pymysql

    # Password is supplied only via the dedicated process environment. No CLI
    # value, application account, app settings or saved credential lookup.
    password = os.getenv("R3_DB_READ_PASSWORD")
    ca = os.getenv("R3_DB_READ_TLS_CA")
    if not password or not ca or not Path(ca).is_file():
        raise ReadFailure("DB_READ_CREDENTIALS_OR_TLS_NOT_CONFIGURED")
    try:
        return pymysql.connect(
            host=grant["host"], port=grant["port"],
            user=grant["principal"].split("@", 1)[0],
            password=password, database=grant["database_name"],
            ssl_ca=ca, ssl_verify_cert=True, ssl_verify_identity=True,
            connect_timeout=5, read_timeout=5, write_timeout=5,
            cursorclass=pymysql.cursors.DictCursor,
            # Do not SET a read-only variable on an unverified session. The
            # approved connector/server must already establish read_only=1.
            autocommit=None, charset="utf8mb4",
        )
    except Exception:
        raise ReadFailure("DB_READ_CONNECT_FAILED") from None


def verify_checkout(commit):
    root = Path(__file__).resolve().parent.parent
    try:
        base = ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root)]
        head = subprocess.run(base + ["rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True, timeout=10).stdout.strip()
        dirty = subprocess.run(base + ["status", "--porcelain", "--untracked-files=normal"],
                               capture_output=True, text=True, check=True, timeout=10).stdout
        if head != commit or dirty:
            raise ReadFailure("CONTROLLER_CHECKOUT_MISMATCH")
    except (OSError, subprocess.SubprocessError):
        raise ReadFailure("CONTROLLER_CHECKOUT_UNVERIFIED") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect-approved-db-baseline", action="store_true", required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--authorization-key-file", type=Path, required=True)
    parser.add_argument("--verifier-key-file", type=Path, required=True)
    parser.add_argument("--controller-commit", required=True)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.journal.resolve() == args.output.resolve():
            raise ReadFailure("DB_OUTPUT_ALREADY_EXISTS_OR_CONFLICTS")
        authorization_key = _load_verifier_key(args.authorization_key_file, args.output)
        _load_verifier_key(args.authorization_key_file, args.authorization)
        if args.journal.parent.resolve() != args.authorization_key_file.parent.resolve():
            raise ReadFailure("READ_JOURNAL_PRIVATE_DIRECTORY_REQUIRED")
        if args.journal.resolve() in {args.authorization_key_file.resolve(), args.verifier_key_file.resolve()}:
            raise ReadFailure("READ_JOURNAL_KEY_CONFLICT")
        collector_key = _load_verifier_key(args.verifier_key_file, args.output)
        if (
            args.authorization_key_file.resolve() == args.verifier_key_file.resolve()
            or authorization_key == collector_key
        ):
            raise ReadFailure("INDEPENDENT_AUTHORIZATION_KEY_REQUIRED")
        verify_checkout(args.controller_commit)
        document = read_document(args.authorization)
        validate_grant(document, key=authorization_key, commit=args.controller_commit, now=time.time())
        issuer = _Issuer(collector_key)
        bundle = collect_approved_baseline(
            document, authorization_key=authorization_key, issuer=issuer,
            commit=args.controller_commit, journal=ReadAttemptJournal(args.journal),
            connect=connect_readonly, revision=args.baseline_revision,
        )
        verify_bundle(bundle, key=collector_key, expected_scope=SCOPE,
                      expected_revision=args.baseline_revision)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(bundle, stream, ensure_ascii=False, indent=2)
        print(json.dumps({"result": "DB_BASELINE_COLLECTED", "production_ready": False,
                          "production_writes": 0, "read_batches": 1}))
        return 0
    except ReadFailure as exc:
        print(json.dumps({"result": "FAIL_CLOSED", "code": exc.code,
                          "production_ready": False, "production_writes": 0}))
    except (OSError, ValueError, TypeError):
        print(json.dumps({"result": "FAIL_CLOSED", "code": "DB_COLLECTION_FAILED",
                          "production_ready": False, "production_writes": 0}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
