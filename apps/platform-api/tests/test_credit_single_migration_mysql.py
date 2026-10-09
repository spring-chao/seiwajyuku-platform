"""Apply 0064-0067 individually in a new disposable CI MySQL schema.

Cloud/backup approval contracts use synthetic attestations. These tests prove
actual SQL effects and durable no-replay behavior, not production readiness.
"""
import json
import os
import re
from uuid import uuid4

import pymysql
import pytest
from sqlalchemy.engine import make_url

from app.migrations import _split_mysql
from app.services.course_credit_canonical import canonical_rule_rows, expected_persisted_rule
from credit_migration_test_support import materials, sign
import credit_single_migration as migrator
from r3_read_evidence import ReadFailure
from test_credit_mapping_migration_version_scope import seed_unrelated_rules


pytestmark = pytest.mark.skipif(os.getenv("CREDIT_MIGRATION_ISOLATED_MYSQL") != "1",
                               reason="explicit disposable MySQL CI only")


@pytest.fixture
def isolated_schema():
    url = make_url(os.environ["DATABASE_URL"])
    assert url.host == "127.0.0.1" and url.username == "b21_test" and url.database == "seiwajyuku_b21_test"
    assert os.environ["APP_ENV"] == "test"
    password = os.environ["CREDIT_MIGRATION_ISOLATED_ROOT_PASSWORD"]
    schema = "credit_migration_ci_" + uuid4().hex[:12]
    assert re.fullmatch(r"credit_migration_ci_[a-f0-9]{12}", schema)
    def connect():
        return pymysql.connect(host="127.0.0.1", port=url.port or 3306, user="root", password=password,
                               database=schema, autocommit=True, cursorclass=pymysql.cursors.DictCursor)
    root = pymysql.connect(host="127.0.0.1", port=url.port or 3306, user="root", password=password, autocommit=True)
    try:
        with root.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
        connection = connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("CREATE TABLE schema_migrations(version VARCHAR(64) PRIMARY KEY,applied_at DATETIME NOT NULL)")
                for path in sorted((migrator.ROOT / "migrations" / "mysql").glob("*.sql")):
                    if path.name[:4] > "0063":
                        break
                    if path.name.startswith("0048_"):
                        # Model the historical pre-existing DRAFT identity:
                        # 0048 INSERT IGNORE must preserve it, leaving mapping
                        # absent. A brand-new install instead publishes it and
                        # is outside this bounded production recovery entry.
                        cursor.execute("INSERT INTO learning_plan_credit_rule_versions(plan_key,version_label,status,based_on_version_label,created_at,updated_at) VALUES ('STANDARD_3Y_2026','2026.1','DRAFT','2026',UTC_TIMESTAMP(),UTC_TIMESTAMP())")
                    for statement in _split_mysql(path.read_text(encoding="utf-8")):
                        cursor.execute(statement)
                    cursor.execute(migrator.STAMP, (path.name,))
                cursor.execute("SELECT id FROM learning_plan_credit_rule_versions WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1'")
                version = cursor.fetchone()["id"]
                cursor.execute("SELECT COUNT(*) AS n FROM learning_plan_credit_rules WHERE rule_version_id=%s", (version,))
                assert cursor.fetchone()["n"] == 0
                for rule in canonical_rule_rows():
                    r = expected_persisted_rule(rule)
                    cursor.execute("INSERT INTO learning_plan_credit_rules(rule_version_id,course_key,course_name,year_index,credit_points,status,source,aliases_json,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(),UTC_TIMESTAMP())",
                        (version, r["course_key"], r["course_name"], r["year_index"], r["credit_points"], r["status"], r["source"], json.dumps(r["aliases"], ensure_ascii=False, separators=(",", ":"))))
                # Synthetic prior rule-APPLY audit in this isolated schema only.
                cursor.execute("INSERT INTO audit_logs(action,resource_type,result,created_at) VALUES ('production.g5_4.course_rule_reconciliation.apply','isolated-fixture','SUCCESS',UTC_TIMESTAMP())")
        finally:
            connection.close()
        yield schema, connect
    finally:
        with root.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{schema}`")
        root.close()


def approved_materials(tmp_path, schema, connect, version):
    connection = connect()
    try:
        rows, baseline = migrator.snapshot(connection)
        # Report a fixed, safe refusal code for fixture setup failures before
        # execute_one deliberately sanitizes database/driver exceptions.
        migrator.preconditions(version, rows, baseline)
        with connection.cursor() as cursor:
            cursor.execute(migrator.ACCESS)
            principal = cursor.fetchone()["principal"]
            cursor.execute(migrator.POLICY_SCOPE)
            count = cursor.fetchone()["count"]
            cursor.execute(migrator.COURSE_FILL)
            fill = cursor.fetchone()["count"]
    finally:
        connection.close()
    grant, args = materials(tmp_path, rows, version=version, database=schema, principal=principal)
    grant.update(expected_policy_binding_count=count, expected_course_reference_fill_count=fill)
    args["connect"] = lambda _: connect()
    return grant, args


def test_mysql_each_approved_migration_is_individual_and_ledger_unchanged(isolated_schema, tmp_path):
    schema, connect = isolated_schema
    # Reused keys in another policy used to make 0064 reject a valid target.
    connection = connect()
    try:
        with connection.cursor() as cursor:
            seed_unrelated_rules(cursor.execute)
            cursor.execute("SELECT r.* FROM learning_plan_credit_rules r JOIN learning_plan_credit_rule_versions v ON v.id=r.rule_version_id WHERE v.plan_key='LEGACY_SCOPE_TEST'")
            unrelated_course = cursor.fetchall()
            cursor.execute("SELECT r.* FROM learning_credit_rules r JOIN learning_credit_rule_versions v ON v.id=r.rule_version_id WHERE v.rule_set_key='LEGACY_SCOPE_TEST'")
            unrelated_generic = cursor.fetchall()
    finally:
        connection.close()
    for version in migrator.MIGRATIONS:
        stage = tmp_path / version
        stage.mkdir()
        grant, args = approved_materials(stage, schema, connect, version)
        result = migrator.execute_one(sign(grant), **args)
        assert result["result"] == "MIGRATION_RECORDED" and result["migration_version"] == version
        assert result["ledger_entry_count"] == 0 and result["ledger_points"] == "0.00"
        connection = connect()
        try:
            rows, after = migrator.snapshot(connection)
            assert after["migration_status"] == {n: "APPLIED" if n <= version else "NOT_APPLIED" for n in migrator.MIGRATIONS}
            with connection.cursor() as cursor:
                cursor.execute("SELECT r.* FROM learning_plan_credit_rules r JOIN learning_plan_credit_rule_versions v ON v.id=r.rule_version_id WHERE v.plan_key='LEGACY_SCOPE_TEST'")
                assert cursor.fetchall() == unrelated_course
                cursor.execute("SELECT r.* FROM learning_credit_rules r JOIN learning_credit_rule_versions v ON v.id=r.rule_version_id WHERE v.rule_set_key='LEGACY_SCOPE_TEST'")
                assert cursor.fetchall() == unrelated_generic
                cursor.execute("SELECT COUNT(*) AS n FROM schema_migrations WHERE version=%s", (migrator.MIGRATIONS[version][0],))
                assert cursor.fetchone()["n"] == 1
                if version == "0067":
                    cursor.execute("SELECT COUNT(*) AS n FROM learning_credit_settlement_batches")
                    assert cursor.fetchone()["n"] == 0
        finally:
            connection.close()
        with pytest.raises(ReadFailure, match="MIGRATION_REPLAY_DISALLOWED"):
            migrator.execute_one(sign(grant), **args)


def test_mysql_first_ddl_persists_on_timeout_and_restart_cannot_replay(isolated_schema, tmp_path):
    schema, connect = isolated_schema
    # This fixture needs the preceding approved migrations, with distinct ledgers.
    for version in ("0064", "0065", "0066"):
        stage = tmp_path / version
        stage.mkdir()
        grant, args = approved_materials(stage, schema, connect, version)
        migrator.execute_one(sign(grant), **args)
    stage = tmp_path / "0067"
    stage.mkdir()
    grant, args = approved_materials(stage, schema, connect, "0067")
    attempts = []
    class InterruptedCursor:
        def __init__(self, cursor):
            self.cursor = cursor
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            self.cursor.close()
        def execute(self, sql, params=None):
            if "CREATE TABLE IF NOT EXISTS learning_credit_settlement_batch_items" in sql:
                raise TimeoutError("isolated loss after first DDL committed")
            return self.cursor.execute(sql, params)
        def __getattr__(self, name):
            return getattr(self.cursor, name)
    class InterruptedConnection:
        def __init__(self, connection):
            self.connection = connection
        def cursor(self):
            return InterruptedCursor(self.connection.cursor())
        def __getattr__(self, name):
            return getattr(self.connection, name)
    def interrupted(_):
        attempts.append(1)
        return InterruptedConnection(connect())
    args["connect"] = interrupted
    with pytest.raises(ReadFailure, match="MIGRATION_OUTCOME_UNKNOWN"):
        migrator.execute_one(sign(grant), **args)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME IN ('learning_credit_settlement_batches','learning_credit_settlement_batch_items')", (schema,))
            assert cursor.fetchall() == [{"TABLE_NAME": "learning_credit_settlement_batches"}]
            cursor.execute("SELECT COUNT(*) AS n FROM schema_migrations WHERE version=%s", (migrator.MIGRATIONS["0067"][0],))
            assert cursor.fetchone()["n"] == 0
            cursor.execute("SELECT COUNT(*) AS n FROM learning_credit_entries")
            assert cursor.fetchone()["n"] == 0
    finally:
        connection.close()
    args["journal"] = migrator.MigrationJournal(args["journal"].path)
    with pytest.raises(ReadFailure, match="MIGRATION_REPLAY_DISALLOWED"):
        migrator.execute_one(sign(grant), **args)
    assert attempts == [1]


@pytest.mark.parametrize("damage", ["course", "generic", "missing_generic"])
def test_mysql_0064_keeps_target_guards_with_reused_keys(isolated_schema, damage):
    _, connect = isolated_schema
    connection = connect()
    try:
        with connection.cursor() as cursor:
            seed_unrelated_rules(cursor.execute)
            if damage == "course":
                cursor.execute("UPDATE learning_plan_credit_rules SET credit_points=999 WHERE course_key='Y1-SIX-DILIGENCES' AND rule_version_id IN (SELECT id FROM learning_plan_credit_rule_versions WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1')")
            elif damage == "generic":
                cursor.execute("UPDATE learning_credit_rules SET points=999 WHERE rule_key='DAILY_READING' AND rule_version_id IN (SELECT id FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1')")
            else:
                cursor.execute("DELETE FROM learning_credit_rules WHERE rule_key='DAILY_READING' AND rule_version_id IN (SELECT id FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1')")
            path = migrator.ROOT / "migrations/mysql" / migrator.MIGRATIONS["0064"][0]
            with pytest.raises(pymysql.err.IntegrityError) as failure:
                for statement in _split_mysql(path.read_text(encoding="utf-8")):
                    cursor.execute(statement)
            assert failure.value.args[0] == 3819  # MySQL CHECK constraint violation.
            connection.rollback()
            cursor.execute("SELECT status FROM learning_plan_credit_rule_versions WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1'")
            assert cursor.fetchone() == {"status": "DRAFT"}
            cursor.execute("SELECT COUNT(*) AS n FROM learning_plan_credit_rule_mappings WHERE plan_key='standard-3y' AND plan_version_label='2026'")
            assert cursor.fetchone()["n"] == 0
            cursor.execute("SELECT COUNT(*) AS n FROM learning_credit_entries")
            assert cursor.fetchone()["n"] == 0
    finally:
        connection.close()
