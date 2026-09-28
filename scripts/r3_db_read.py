"""Bounded SELECT-only baseline port. Live production DB access is not enabled.

No SQL argument API, transaction commands, application DB helpers, or writers.
An independently approved read-only principal/connection is required; possession
of credentials or passing an arbitrary boolean is not authorization.
"""

from __future__ import annotations

import json
from decimal import Decimal
from enum import Enum
from types import MappingProxyType

from r3_read_evidence import ReadFailure, fingerprint


class QueryId(str, Enum):
    ACCESS = "R3_ACCESS_BASELINE"
    PRIVILEGES = "R3_PRIVILEGES_BASELINE"
    VERSION = "R3_RULE_VERSION_BASELINE"
    RULES = "R3_RULE_BASELINE"
    GENERIC_VERSION = "R3_GENERIC_VERSION_BASELINE"
    GENERIC_RULES = "R3_GENERIC_RULE_BASELINE"
    MAPPING = "R3_MAPPING_BASELINE"
    BINDING = "R3_BINDING_BASELINE"
    LEDGER = "R3_LEDGER_BASELINE"
    MIGRATION = "R3_MIGRATION_BASELINE"
    APPLY_AUDIT = "R3_APPLY_AUDIT_BASELINE"
    PLACEHOLDER = "R3_PLACEHOLDER_BASELINE"


VERSION_FILTER = "plan_key='STANDARD_3Y_2026' AND version_label='2026.1'"
GENERIC_FILTER = "rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1'"
PLACEHOLDERS = "('AUTO-QR-EXCELLENT-IMPROVEMENT','AUTO-QR-HAPPINESS-CARE','AUTO-QR-IMPROVEMENT-INNOVATION')"
SQL = MappingProxyType(
    {
        QueryId.ACCESS: "SELECT DATABASE() AS database_name, CURRENT_USER() AS principal, CURRENT_ROLE() AS roles, @@session.transaction_read_only AS read_only",
        QueryId.PRIVILEGES: "SELECT PRIVILEGE_TYPE AS privilege_type FROM information_schema.USER_PRIVILEGES WHERE GRANTEE=CONCAT(CHAR(39),SUBSTRING_INDEX(CURRENT_USER(),'@',1),CHAR(39),'@',CHAR(39),SUBSTRING_INDEX(CURRENT_USER(),'@',-1),CHAR(39)) UNION ALL SELECT PRIVILEGE_TYPE FROM information_schema.SCHEMA_PRIVILEGES WHERE GRANTEE=CONCAT(CHAR(39),SUBSTRING_INDEX(CURRENT_USER(),'@',1),CHAR(39),'@',CHAR(39),SUBSTRING_INDEX(CURRENT_USER(),'@',-1),CHAR(39)) UNION ALL SELECT PRIVILEGE_TYPE FROM information_schema.TABLE_PRIVILEGES WHERE GRANTEE=CONCAT(CHAR(39),SUBSTRING_INDEX(CURRENT_USER(),'@',1),CHAR(39),'@',CHAR(39),SUBSTRING_INDEX(CURRENT_USER(),'@',-1),CHAR(39)) UNION ALL SELECT PRIVILEGE_TYPE FROM information_schema.COLUMN_PRIVILEGES WHERE GRANTEE=CONCAT(CHAR(39),SUBSTRING_INDEX(CURRENT_USER(),'@',1),CHAR(39),'@',CHAR(39),SUBSTRING_INDEX(CURRENT_USER(),'@',-1),CHAR(39))",
        QueryId.VERSION: f"SELECT id, plan_key, version_label, status, based_on_version_label FROM learning_plan_credit_rule_versions WHERE {VERSION_FILTER}",
        QueryId.RULES: f"SELECT id, course_key, course_name, year_index, credit_points, status, source, aliases_json FROM learning_plan_credit_rules WHERE rule_version_id IN (SELECT id FROM learning_plan_credit_rule_versions WHERE {VERSION_FILTER}) ORDER BY course_key,id",
        QueryId.GENERIC_VERSION: f"SELECT id,status FROM learning_credit_rule_versions WHERE {GENERIC_FILTER}",
        QueryId.GENERIC_RULES: f"SELECT rule_key,settlement_model,points,cap_points,status FROM learning_credit_rules WHERE rule_version_id IN (SELECT id FROM learning_credit_rule_versions WHERE {GENERIC_FILTER}) ORDER BY rule_key",
        QueryId.MAPPING: "SELECT COUNT(*) AS count FROM learning_plan_credit_rule_mappings WHERE plan_key='standard-3y' AND plan_version_label='2026'",
        QueryId.BINDING: "SELECT COUNT(*) AS count, COALESCE(SUM(b.credit_rule_version_id IS NOT NULL),0) AS generic_frozen, COALESCE(SUM(b.course_credit_rule_version_id IS NOT NULL),0) AS course_frozen FROM class_learning_bindings b JOIN learning_plan_versions p ON p.id=b.plan_version_id WHERE b.status='ACTIVE' AND p.plan_key='standard-3y' AND p.version_label='2026'",
        QueryId.LEDGER: "SELECT COUNT(*) AS count, COALESCE(SUM(points),0) AS points FROM learning_credit_entries",
        QueryId.MIGRATION: "SELECT version FROM schema_migrations WHERE version IN ('0064_fix_credit_rule_mapping_and_binding_freeze.sql','0065_learning_credit_history_import.sql','0066_historical_credit_time_precision_review.sql') ORDER BY version",
        QueryId.APPLY_AUDIT: "SELECT COUNT(*) AS count FROM audit_logs WHERE action='production.g5_4.course_rule_reconciliation.apply'",
        QueryId.PLACEHOLDER: f"SELECT (SELECT COUNT(*) FROM study_meeting_sessions WHERE course_key IN {PLACEHOLDERS}) + (SELECT COUNT(*) FROM study_meeting_courses WHERE course_key IN {PLACEHOLDERS}) + (SELECT COUNT(*) FROM study_meeting_course_completions c JOIN study_meeting_courses sc ON sc.id=c.study_meeting_course_id WHERE sc.course_key IN {PLACEHOLDERS}) + (SELECT COUNT(*) FROM learning_credit_entries WHERE rule_key IN {PLACEHOLDERS}) + (SELECT COUNT(*) FROM learning_plan_credit_rule_mappings WHERE generic_rule_version_id IN (SELECT id FROM learning_credit_rule_versions WHERE {GENERIC_FILTER}) OR course_credit_rule_version_id IN (SELECT id FROM learning_plan_credit_rule_versions WHERE {VERSION_FILTER})) AS count",
    }
)


def _normalize(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


class MysqlSelectPort:
    """Only fixed QueryId is accepted; connection never returned to caller.

    The connection must already use a read-only account and session. This port
    deliberately does not SET variables or provision permissions. No CLI path
    constructs it against production in Phase 3.
    """

    def __init__(self, connection):
        self.__connection = connection

    def read(self, query_id):
        if type(query_id) is not QueryId:
            raise ReadFailure("QUERY_NOT_ALLOWED")
        try:
            with self.__connection.cursor() as cursor:
                cursor.execute(SQL[query_id])
                rows = cursor.fetchmany(501)
                if len(rows) > 500:
                    raise ReadFailure("QUERY_RESULT_TOO_LARGE")
                return _normalize(list(rows))
        except ReadFailure:
            raise
        except Exception:  # noqa: BLE001 - DB exceptions can contain credentials
            raise ReadFailure("DB_READ_FAILED") from None


def _one(rows, fields):
    if len(rows) != 1 or any(k not in rows[0] for k in fields):
        raise ReadFailure("DB_FIELDS_MISSING")
    return rows[0]


def _count(value):
    if type(value) is not int or value < 0:
        raise ReadFailure("DB_COUNT_INVALID")
    return value


def derive_baseline(rows):
    # Pure canonical policy functions; no production_operations/DB writer import.
    from app.services.course_credit_canonical import canonical_fingerprint
    from app.services.course_credit_reconciliation import production_fingerprint

    v = _one(
        rows[QueryId.VERSION],
        ("id", "plan_key", "version_label", "status", "based_on_version_label"),
    )
    rules = rows[QueryId.RULES]
    for rule in rules:
        if any(
            k not in rule
            for k in (
                "id",
                "course_key",
                "course_name",
                "year_index",
                "credit_points",
                "status",
                "source",
                "aliases_json",
            )
        ):
            raise ReadFailure("DB_FIELDS_MISSING")
        if not isinstance(json.loads(rule["aliases_json"]), list):
            raise ReadFailure("DB_ALIASES_INVALID")
    generic_versions = rows[QueryId.GENERIC_VERSION]
    generic = rows[QueryId.GENERIC_RULES]
    for rule in generic:
        if any(
            k not in rule
            for k in ("rule_key", "settlement_model", "points", "cap_points", "status")
        ):
            raise ReadFailure("DB_FIELDS_MISSING")
    for version in generic_versions:
        if any(k not in version for k in ("id", "status")):
            raise ReadFailure("DB_FIELDS_MISSING")
    bindings = _one(rows[QueryId.BINDING], ("count", "generic_frozen", "course_frozen"))
    ledger = _one(rows[QueryId.LEDGER], ("count", "points"))
    migrations = [x["version"] for x in rows[QueryId.MIGRATION]]
    points = Decimal(str(ledger["points"]))
    if not points.is_finite():
        raise ReadFailure("DB_POINTS_INVALID")
    result = {
        "course_rule_version_id": v["id"],
        "course_rule_status": v["status"],
        "rule_count": len(rules),
        "production_fingerprint": production_fingerprint(v, rules),
        "canonical_fingerprint": canonical_fingerprint(),
        "generic_rule_version_count": len(generic_versions),
        "generic_rule_statuses": [x["status"] for x in generic_versions],
        "generic_active_rule_count": sum(x["status"] == "ACTIVE" for x in generic),
        "generic_rules_fingerprint": fingerprint(generic),
        "mapping_count": _count(_one(rows[QueryId.MAPPING], ("count",))["count"]),
        "active_binding_count": _count(bindings["count"]),
        "generic_frozen_count": _count(bindings["generic_frozen"]),
        "course_frozen_count": _count(bindings["course_frozen"]),
        "ledger_entry_count": _count(ledger["count"]),
        "ledger_points": format(points, ".2f"),
        "migration_status": {
            n: "APPLIED"
            if any(x.startswith(n + "_") for x in migrations)
            else "NOT_APPLIED"
            for n in ("0064", "0065", "0066")
        },
        "apply_audit_count": _count(
            _one(rows[QueryId.APPLY_AUDIT], ("count",))["count"]
        ),
        "placeholder_reference_count": _count(
            _one(rows[QueryId.PLACEHOLDER], ("count",))["count"]
        ),
    }
    return result


class DatabaseReadAdapter:
    def __init__(self, issuer, port, *, scope, database_name):
        self._issuer, self.__port = issuer, port
        self.__scope, self.__database = scope, database_name

    def collect_fixture(self):
        if self._issuer.realm != "fixture":
            raise ReadFailure("PRODUCTION_DB_READ_NOT_AUTHORIZED")
        return self._collect()

    def collect(self):
        # Current authorization does not name access method, tables and principal.
        # No boolean/CLI override can activate production business-data reads.
        raise ReadFailure("PRODUCTION_DB_READ_NOT_AUTHORIZED")

    def _collect(self):
        start = self._issuer.clock()
        traces = []
        target_fingerprint = fingerprint(
            {"scope": self.__scope, "database": self.__database}
        )

        def read(query_id):
            observed_at = self._issuer.clock()
            rows = self.__port.read(query_id)
            traces.append(
                {
                    "query_id": query_id.value,
                    "observed_at": observed_at,
                    "completed_at": self._issuer.clock(),
                    "attempt_count": 1,
                    "connection_target_fingerprint": target_fingerprint,
                    "result_fingerprint": fingerprint(rows),
                }
            )
            return rows

        access = _one(
            read(QueryId.ACCESS),
            ("database_name", "principal", "roles", "read_only"),
        )
        if (
            access["database_name"] != self.__database
            or access["read_only"] != 1
            or access["roles"] != "NONE"
        ):
            raise ReadFailure("DB_NOT_VERIFIED_READONLY")
        privileges = read(QueryId.PRIVILEGES)
        if not privileges or any(
            x.get("privilege_type") not in {"SELECT", "USAGE"} for x in privileges
        ):
            raise ReadFailure("DB_NOT_VERIFIED_READONLY")
        query_ids = [
            x for x in QueryId if x not in (QueryId.ACCESS, QueryId.PRIVILEGES)
        ]
        before = {q: read(q) for q in query_ids}
        after = {q: read(q) for q in query_ids}
        if (
            before != after
            or read(QueryId.ACCESS) != [access]
            or read(QueryId.PRIVILEGES) != privileges
        ):
            raise ReadFailure("SNAPSHOT_CHANGED")
        data = derive_baseline(before)
        data["connection_target_fingerprint"] = target_fingerprint
        data["read_trace"] = traces
        data["query_results"] = {q.value: fingerprint(before[q]) for q in query_ids}
        return self._issuer._issue(
            "DatabaseBaselineEvidence",
            "mysql-readonly:R3_BASELINE",
            self.__scope,
            before,
            data,
            start,
        )
