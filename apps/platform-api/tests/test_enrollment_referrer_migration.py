"""Validate additive referrals in memory and an explicitly disposable CI MySQL schema."""
import os
import re
import sqlite3
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.migrations import MIGRATION_ROOT, _split_mysql


@pytest.fixture(params=["sqlite", "mysql"])
def migration_db(request):
    dialect = request.param
    root = None
    schema = None
    if dialect == "sqlite":
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys=ON")
    else:
        if os.environ.get("ENROLLMENT_MIGRATION_ISOLATED_MYSQL") != "1":
            pytest.skip("explicit isolated MySQL CI only")
        import pymysql
        url = urlparse(os.environ["DATABASE_URL"])
        assert os.environ["APP_ENV"] == "staging"
        assert url.hostname == "127.0.0.1" and url.path == "/seiwajyuku_staging"
        schema = "enrollment_referral_ci_" + uuid4().hex[:12]
        assert re.fullmatch(r"enrollment_referral_ci_[a-f0-9]{12}", schema)
        kwargs = dict(host="127.0.0.1", port=url.port or 3306, user="root",
            password=os.environ["ENROLLMENT_MIGRATION_ROOT_PASSWORD"], autocommit=True)
        root = pymysql.connect(**kwargs)
        with root.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
        connection = pymysql.connect(**kwargs, database=schema)

    def sql(statement):
        cursor = connection.cursor()
        try:
            cursor.execute(statement)
            return cursor.fetchall()
        finally:
            cursor.close()

    def script(path):
        source = path.read_text(encoding="utf-8")
        if dialect == "sqlite":
            connection.executescript(source)
        else:
            for statement in _split_mysql(source):
                sql(statement)

    try:
        # Parent key types match the platform. These are synthetic fixtures in
        # the new disposable schema, never rows from the staging service DB.
        sql("CREATE TABLE app_users (id BIGINT PRIMARY KEY)")
        sql("CREATE TABLE org_units (id VARCHAR(64) PRIMARY KEY)")
        sql("CREATE TABLE member_enrollment_applications (id BIGINT PRIMARY KEY, name VARCHAR(255))")
        sql("CREATE TABLE schema_migrations (version VARCHAR(64) PRIMARY KEY, applied_at VARCHAR(64))")
        sql("INSERT INTO app_users VALUES (7)")
        sql("INSERT INTO org_units VALUES ('test-referrer-center')")
        sql("INSERT INTO member_enrollment_applications VALUES (42, 'Synthetic original application')")
        if dialect == "sqlite":
            connection.commit()
        yield dialect, connection, sql, script
    finally:
        connection.close()
        if root is not None:
            with root.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{schema}`")
            root.close()


def test_referrer_migration_is_additive_idempotent_and_empty_rollback_safe(migration_db):
    dialect, connection, sql, script = migration_db
    forward = MIGRATION_ROOT / dialect / "0068_enrollment_referrer_centers.sql"
    script(forward)
    script(forward)
    assert sql("SELECT name FROM member_enrollment_applications WHERE id=42")[0][0] == "Synthetic original application"
    assert sql("SELECT COUNT(*) FROM enrollment_application_referrer_centers")[0][0] == 0
    script(MIGRATION_ROOT / "rollback" / dialect / "0068_enrollment_referrer_centers.down.sql")
    assert sql("SELECT name FROM member_enrollment_applications WHERE id=42")[0][0] == "Synthetic original application"
    script(forward)
    assert sql("SELECT COUNT(*) FROM enrollment_application_referrer_centers")[0][0] == 0


def test_referrer_foreign_keys_and_populated_rollback_guard(migration_db):
    dialect, connection, sql, script = migration_db
    script(MIGRATION_ROOT / dialect / "0068_enrollment_referrer_centers.sql")
    with pytest.raises(Exception):
        sql("INSERT INTO enrollment_application_referrer_centers VALUES (42,'missing-center',7,'2026-10-09')")
    sql("INSERT INTO enrollment_application_referrer_centers VALUES (42,'test-referrer-center',7,'2026-10-09')")
    connection.commit()
    with pytest.raises(Exception):
        script(MIGRATION_ROOT / "rollback" / dialect / "0068_enrollment_referrer_centers.down.sql")
    connection.rollback()
    assert sql("SELECT referrer_org_unit_id FROM enrollment_application_referrer_centers WHERE application_id=42")[0][0] == "test-referrer-center"
    assert sql("SELECT name FROM member_enrollment_applications WHERE id=42")[0][0] == "Synthetic original application"
