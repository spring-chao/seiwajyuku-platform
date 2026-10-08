import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import r3_collect_db_readonly as collector
import r3_db_read as db
from r3_authorization import ReadAttemptJournal
from r3_read_evidence import ReadFailure, _Issuer, fingerprint, verify_bundle
from test_r3_readonly_evidence import db_rows  # noqa: F401 - shared synthetic rows

NOW = 1000.0
AUTH_KEY = b"independent-synthetic-authorizer-00000000"
COLLECTOR_KEY = b"independent-synthetic-collector-00000000"
COMMIT = "c" * 40
REVISION = collector.SERVICE + "-123"
JOURNAL_HASH = "j" * 64


@pytest.fixture(autouse=True)
def bind_test_journal(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.modules[__name__], "JOURNAL_HASH", fingerprint((tmp_path / "reads.sqlite").resolve().as_posix()))


def grant(**changes):
    data = {
        "schema_version": 1, "purpose": "R3_DB_BASELINE_READ",
        "scope": collector.SCOPE, "controller_commit": COMMIT,
        "approval_ref": "TEST-APPROVAL-001", "approved_by": "synthetic-reviewer",
        "realm": "live", "not_before": NOW - 1, "expires_at": NOW + 100,
        "database_name": "fixture", "host": "127.0.0.1", "port": 3306,
        "principal": "readonly@host", "query_fingerprint": collector.query_fingerprint(),
        "baseline_revision": REVISION,
        "journal_fingerprint": JOURNAL_HASH,
        "max_batches": 1, "max_writes": 0,
        **changes,
    }
    return {**data, "seal": _Issuer(AUTH_KEY)._mac(data)}


class Connection:
    def __init__(self, rows):
        self.rows, self.calls, self.closed = rows, [], False

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql):
        query_id = next(q for q, fixed in db.SQL.items() if fixed == sql)
        self.calls.append(query_id)
        self.current = query_id

    def fetchmany(self, count):
        return copy.deepcopy(self.rows[self.current][:count])

    def close(self):
        self.closed = True


@pytest.fixture
def setup(tmp_path, db_rows):
    db_rows[db.QueryId.PRIVILEGES] = [{
        "grant_scope": "COLUMN", "database_name": "fixture",
        "table_name": "learning_credit_entries", "column_name": "points",
        "privilege_type": "SELECT",
        "is_grantable": "NO",
    }]
    connection = Connection(db_rows)
    calls = []

    def connect(approved):
        calls.append(approved)
        return connection

    args = dict(
        authorization_key=AUTH_KEY, commit=COMMIT,
        issuer=_Issuer(COLLECTOR_KEY, clock=lambda: NOW),
        journal=ReadAttemptJournal(tmp_path / "reads.sqlite"),
        connect=connect, revision=REVISION, clock=lambda: NOW,
    )
    return args, connection, calls


def test_authorized_collection_returns_only_signed_derived_baseline(setup):
    args, connection, calls = setup
    bundle = collector.collect_approved_baseline(grant(), **args)
    records = verify_bundle(bundle, key=COLLECTOR_KEY, expected_scope=collector.SCOPE,
                            expected_revision=REVISION, now=NOW)
    assert records[0].payload()["active_binding_count"] == 19
    assert records[0].payload()["ledger_entry_count"] == 0
    assert bundle["production_ready"] is False
    assert len(calls) == 1 and connection.closed
    assert len(connection.calls) == 24
    with pytest.raises(ReadFailure, match="REPLAY_DISALLOWED"):
        collector.collect_approved_baseline(grant(), **args)
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"purpose": "R3_APPLY"}, {"scope": "wrong-service"},
    {"controller_commit": "a" * 40}, {"realm": "fixture"},
    {"not_before": NOW + 1}, {"expires_at": NOW},
    {"expires_at": float("inf")}, {"not_before": True},
    {"max_writes": 1}, {"max_writes": False}, {"max_batches": 2},
    {"max_batches": True}, {"query_fingerprint": "a" * 64},
    {"database_name": "fixture;DROP TABLE ledger"}, {"host": "u:p@host"},
    {"port": True}, {"principal": "root"}, {"approved_by": ""},
    {"approval_ref": ""}, {"extra": "UPDATE"},
    {"baseline_revision": "another-service-123"},
])
def test_invalid_authorization_never_opens_connection(setup, changes):
    args, connection, calls = setup
    if changes.get("expires_at") == float("inf"):
        doc = grant()
        doc["expires_at"] = float("inf")
    else:
        doc = grant(**changes)
    with pytest.raises(ReadFailure):
        collector.collect_approved_baseline(doc, **args)
    assert calls == [] and connection.calls == []


def test_signature_mutation_rejected_before_network(setup):
    args, _, calls = setup
    doc = grant()
    doc["principal"] = "root@localhost"
    with pytest.raises(ReadFailure, match="SIGNATURE"):
        collector.collect_approved_baseline(doc, **args)
    assert calls == []


@pytest.mark.parametrize("field,value", [
    ("principal", "another@host"), ("database_name", "another_database"),
    ("roles", "admin"), ("read_only", 0),
])
def test_access_mismatch_stops_before_business_queries(setup, field, value):
    args, connection, _ = setup
    connection.rows[db.QueryId.ACCESS][0][field] = value
    with pytest.raises(ReadFailure):
        collector.collect_approved_baseline(grant(), **args)
    assert connection.calls == [db.QueryId.ACCESS] and connection.closed


@pytest.mark.parametrize("changes", [
    {"grant_scope": "GLOBAL"}, {"grant_scope": "SCHEMA"},
    {"grant_scope": "TABLE"}, {"database_name": "other"},
    {"table_name": "members"}, {"column_name": "member_id"},
    {"privilege_type": "UPDATE"}, {"privilege_type": "FILE"},
    {"is_grantable": "YES"},
])
def test_broad_or_sensitive_privileges_rejected_before_business_queries(setup, changes):
    args, connection, _ = setup
    connection.rows[db.QueryId.PRIVILEGES][0].update(changes)
    with pytest.raises(ReadFailure, match="PRIVILEGE_SCOPE"):
        collector.collect_approved_baseline(grant(), **args)
    assert connection.calls == [db.QueryId.ACCESS, db.QueryId.PRIVILEGES]
    assert connection.closed


def test_unknown_connect_outcome_is_spent_across_restart(setup, tmp_path):
    args, _, calls = setup

    def fail_connect(_):
        calls.append(1)
        raise RuntimeError("password=DO_NOT_LOG")

    args["connect"] = fail_connect
    with pytest.raises(ReadFailure, match="DB_READ_FAILED") as caught:
        collector.collect_approved_baseline(grant(), **args)
    assert "DO_NOT_LOG" not in str(caught.value)
    args["journal"] = ReadAttemptJournal(tmp_path / "reads.sqlite")
    with pytest.raises(ReadFailure, match="REPLAY_DISALLOWED"):
        collector.collect_approved_baseline(grant(), **args)
    assert calls == [1]


def test_different_journal_cannot_reuse_approval(setup, tmp_path):
    args, _, calls = setup
    args["journal"] = ReadAttemptJournal(tmp_path / "another.sqlite")
    with pytest.raises(ReadFailure, match="JOURNAL_TARGET_MISMATCH"):
        collector.collect_approved_baseline(grant(), **args)
    assert calls == []


def test_fixture_issuer_cannot_reach_live_connection(setup):
    args, _, calls = setup
    args["issuer"] = _Issuer(COLLECTOR_KEY, realm="fixture", clock=lambda: NOW)
    with pytest.raises(ReadFailure, match="COLLECTOR_IDENTITY"):
        collector.collect_approved_baseline(grant(), **args)
    assert calls == []


def test_revision_not_approved_cannot_reach_live_connection(setup):
    args, _, calls = setup
    args["revision"] = collector.SERVICE + "-124"
    with pytest.raises(ReadFailure, match="COLLECTOR_IDENTITY"):
        collector.collect_approved_baseline(grant(), **args)
    assert calls == []


def test_expiry_between_queries_closes_connection(setup):
    args, connection, _ = setup
    ticks = iter([NOW, NOW, NOW, NOW + 200])
    args["clock"] = lambda: next(ticks)
    with pytest.raises(ReadFailure, match="EXPIRED"):
        collector.collect_approved_baseline(grant(), **args)
    assert connection.closed
    assert connection.calls == [db.QueryId.ACCESS]


def test_dedicated_connector_requires_tls_and_never_uses_app_credentials(monkeypatch):
    monkeypatch.delenv("R3_DB_READ_PASSWORD", raising=False)
    monkeypatch.setenv("DATABASE_URL", "mysql://root:DO_NOT_USE@production/db")
    with pytest.raises(ReadFailure, match="NOT_CONFIGURED"):
        collector.connect_readonly(grant())


def test_connector_has_fixed_tls_timeouts_and_no_session_write(monkeypatch, tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("synthetic certificate path", encoding="utf-8")
    monkeypatch.setenv("R3_DB_READ_PASSWORD", "synthetic-private-password")
    monkeypatch.setenv("R3_DB_READ_TLS_CA", str(ca))
    called = []
    import pymysql
    monkeypatch.setattr(pymysql, "connect", lambda **kwargs: called.append(kwargs))
    collector.connect_readonly(grant())
    assert len(called) == 1
    kwargs = called[0]
    assert kwargs["ssl_verify_cert"] is True and kwargs["ssl_verify_identity"] is True
    assert kwargs["connect_timeout"] == kwargs["read_timeout"] == kwargs["write_timeout"] == 5
    assert kwargs["autocommit"] is None and "init_command" not in kwargs


def test_cli_missing_independent_key_never_opens_db(monkeypatch, tmp_path, capsys):
    called = []
    monkeypatch.setattr(collector, "connect_readonly", lambda *args: called.append(1))
    args = ["--collect-approved-db-baseline", "--authorization", str(tmp_path / "grant.json"),
            "--authorization-key-file", str(tmp_path / "missing-auth-key"),
            "--verifier-key-file", str(tmp_path / "missing-collector-key"),
            "--controller-commit", COMMIT, "--baseline-revision", REVISION,
            "--journal", str(tmp_path / "reads.sqlite"), "--output", str(tmp_path / "bundle.json")]
    assert collector.main(args) == 2 and called == []
    assert "FAIL_CLOSED" in capsys.readouterr().out
    assert not (tmp_path / "reads.sqlite").exists()
