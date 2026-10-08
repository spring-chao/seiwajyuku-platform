"""Real column-scoped account in the disposable C7 MySQL CI service only."""

import os
import re
import sys
import time
from pathlib import Path

import pymysql
import pytest
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import r3_collect_db_readonly as collector
from r3_authorization import ReadAttemptJournal
from r3_read_evidence import ReadFailure, _Issuer, fingerprint, verify_bundle

pytestmark = pytest.mark.skipif(
    os.getenv("R3_ISOLATED_DB_READ_MYSQL") != "1", reason="isolated CI MySQL opt-in only",
)

AUTH_KEY = b"isolated-ci-approval-key-0000000000000"
KEY = b"isolated-ci-collector-key-000000000000"
PASSWORD = "ci-r3-readonly-synthetic-password"
USER = "r3_ci_readonly"
COMMIT = "c" * 40
REVISION = collector.SERVICE + "-123"


@pytest.fixture
def isolated_account():
    url = make_url(os.environ["DATABASE_URL"])
    assert url.host == "127.0.0.1" and url.username == "c7_test"
    assert re.fullmatch(r"[A-Za-z0-9_]+_ci", url.database)
    assert os.environ.get("APP_ENV") == "test"
    root_password = os.environ["R3_ISOLATED_MYSQL_ROOT_PASSWORD"]
    root = pymysql.connect(host="127.0.0.1", port=url.port or 3306,
                           user="root", password=root_password,
                           database=url.database, autocommit=True)
    try:
        with root.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            assert cursor.fetchone()[0] == url.database
            cursor.execute(f"CREATE USER '{USER}'@'%' IDENTIFIED BY %s", (PASSWORD,))
            for table, columns in collector.APPROVED_COLUMNS.items():
                selected = ",".join(f"`{column}`" for column in sorted(columns))
                cursor.execute(f"GRANT SELECT ({selected}) ON `{url.database}`.`{table}` TO '{USER}'@'%'")
        yield url, root
    finally:
        with root.cursor() as cursor:
            cursor.execute(f"DROP USER IF EXISTS '{USER}'@'%'")
        root.close()


def signed_grant(url, journal):
    now = time.time()
    data = {
        "schema_version": 1, "purpose": "R3_DB_BASELINE_READ",
        "scope": collector.SCOPE, "controller_commit": COMMIT,
        "approval_ref": "ISOLATED-CI-R3-READ-001", "approved_by": "ci-synthetic-reviewer",
        "realm": "live", "not_before": now - 1, "expires_at": now + 120,
        "database_name": url.database, "host": "127.0.0.1", "port": url.port or 3306,
        "principal": USER + "@%", "query_fingerprint": collector.query_fingerprint(),
        "baseline_revision": REVISION,
        "journal_fingerprint": fingerprint(journal.path.resolve().as_posix()),
        "max_batches": 1, "max_writes": 0,
    }
    return {**data, "seal": _Issuer(AUTH_KEY)._mac(data)}


def factory(url):
    def connect(_):
        # CI setup preconfigures only its synthetic connection; the production
        # collector itself never SETs a session variable or creates an account.
        return pymysql.connect(host="127.0.0.1", port=url.port or 3306,
            user=USER, password=PASSWORD, database=url.database, autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
            init_command="SET SESSION TRANSACTION READ ONLY")
    return connect


def test_fixed_queries_work_with_real_column_permissions_and_no_ledger_write(isolated_account, tmp_path):
    url, root = isolated_account
    with root.cursor() as cursor:
        cursor.execute("SELECT COUNT(*),COALESCE(SUM(points),0) FROM learning_credit_entries")
        before = cursor.fetchone()
    journal = ReadAttemptJournal(tmp_path / "reads.sqlite")
    doc = signed_grant(url, journal)
    bundle = collector.collect_approved_baseline(doc, authorization_key=AUTH_KEY,
        issuer=_Issuer(KEY), commit=COMMIT, journal=journal,
        connect=factory(url), revision=REVISION)
    record, = verify_bundle(bundle, key=KEY, expected_scope=collector.SCOPE,
                            expected_revision=REVISION)
    assert record.payload()["ledger_entry_count"] == before[0]
    assert len(record.payload()["read_trace"]) == 24
    with root.cursor() as cursor:
        cursor.execute("SELECT COUNT(*),COALESCE(SUM(points),0) FROM learning_credit_entries")
        assert cursor.fetchone() == before
    connection = factory(url)(doc)
    try:
        with connection.cursor() as cursor:
            with pytest.raises(pymysql.err.OperationalError):
                cursor.execute("SELECT member_id FROM learning_credit_entries")
            with pytest.raises(pymysql.err.OperationalError):
                cursor.execute("DELETE FROM learning_credit_entries")
    finally:
        connection.close()


def test_table_wide_select_rejected_by_real_privilege_metadata(isolated_account, tmp_path):
    url, root = isolated_account
    with root.cursor() as cursor:
        cursor.execute(f"GRANT SELECT ON `{url.database}`.learning_credit_entries TO '{USER}'@'%'")
    journal = ReadAttemptJournal(tmp_path / "reads.sqlite")
    with pytest.raises(ReadFailure, match="DB_PRIVILEGE_SCOPE_UNVERIFIED"):
        collector.collect_approved_baseline(signed_grant(url, journal),
            authorization_key=AUTH_KEY, issuer=_Issuer(KEY), commit=COMMIT,
            journal=journal, connect=factory(url), revision=REVISION)
