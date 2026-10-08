import copy
import json
import os
import subprocess
import sys

import pytest

from credit_migration_test_support import KEY, NOW, materials, ready_rows, sign
import credit_single_migration as migrator
from r3_db_read import QueryId, SQL
from r3_read_evidence import ReadFailure, _Issuer
from test_r3_readonly_evidence import db_rows  # noqa: F401


class Connection:
    def __init__(self, rows):
        self.rows, self.calls, self.closed = rows, [], False
        self.full_scope = copy.deepcopy(rows[QueryId.BINDING][0])
        self.commit_fails = False
        self.ddl_fails = False

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self.current = sql
        if self.ddl_fails and "CREATE TABLE IF NOT EXISTS learning_credit_settlement_batch_items" in sql:
            raise TimeoutError("sensitive-driver-detail-must-not-escape")

    def fetchone(self):
        if self.current == migrator.ACCESS:
            return {"database_name": "fixture", "principal": "writer@host", "roles": "NONE"}
        if self.current == migrator.LOCK:
            return {"acquired": 1}
        if self.current == migrator.DEPENDENCY:
            return {"version": "0063_complete_changzhou_wuxi_fee_and_service_address.sql"}
        if self.current == migrator.POLICY_SCOPE:
            return self.full_scope
        if self.current == migrator.COURSE_FILL:
            return {"count": 0}
        raise AssertionError("unexpected fixed query")

    def fetchmany(self, size):
        q = next(q for q in QueryId if SQL[q] == self.current)
        return copy.deepcopy(self.rows[q][:size])

    def commit(self):
        if self.commit_fails:
            raise TimeoutError("sensitive-driver-detail-must-not-escape")

    def close(self):
        self.closed = True


@pytest.fixture
def setup(tmp_path, db_rows):
    rows = ready_rows(db_rows)
    grant, args = materials(tmp_path, rows)
    connection = Connection(rows)
    connections = []
    def connect(approved):
        connections.append(approved)
        return connection
    args["connect"] = connect
    return grant, args, connection, connections


def test_fixed_0067_execute_records_exact_script_once_and_preserves_ledger(setup):
    grant, args, connection, connections = setup
    result = migrator.execute_one(sign(grant), **args)
    assert result["result"] == "MIGRATION_RECORDED" and result["replay_allowed"] is False
    assert result["ledger_entry_count"] == 0 and result["ledger_points"] == "0.00"
    p, statements = migrator.plan("0067")
    assert [sql for sql, _ in connection.calls if sql in statements] == statements
    assert connection.calls[-1] == (migrator.STAMP, (p["filename"],))
    assert connection.closed and len(connections) == 1
    args["journal"] = migrator.MigrationJournal(args["journal"].path)
    with pytest.raises(ReadFailure, match="MIGRATION_REPLAY_DISALLOWED"):
        migrator.execute_one(sign(grant), **args)
    assert len(connections) == 1


@pytest.mark.parametrize("changes", [
    {"max_runs": 2}, {"max_runs": True}, {"purpose": "R3_DB_BASELINE_READ"},
    {"scope": "another-service"}, {"migration_version": "0066"}, {"migration_sha256": "0" * 64},
    {"controller_commit": "a" * 40}, {"realm": "fixture"}, {"expires_at": NOW - 1},
    {"database_name": True}, {"host": None}, {"migration_statement_count": True}, {"unknown": "value"},
])
def test_bad_independent_approval_never_connects(setup, changes):
    grant, args, _, connections = setup
    with pytest.raises(ReadFailure):
        migrator.execute_one(sign({**grant, **changes}), **args)
    assert connections == []


@pytest.mark.parametrize("failure", ["ddl_fails", "commit_fails"])
def test_partial_ddl_or_unknown_commit_spends_budget_across_restart_and_new_approval(setup, failure):
    grant, args, connection, connections = setup
    setattr(connection, failure, True)
    with pytest.raises(ReadFailure, match="MIGRATION_OUTCOME_UNKNOWN"):
        migrator.execute_one(sign(grant), **args)
    assert connection.closed
    args["journal"] = migrator.MigrationJournal(args["journal"].path)
    with pytest.raises(ReadFailure, match="MIGRATION_REPLAY_DISALLOWED"):
        migrator.execute_one(sign({**grant, "approval_ref": "NEW-ISOLATED-APPROVAL"}), **args)
    assert len(connections) == 1


def test_live_baseline_drift_prevents_any_ddl(setup):
    grant, args, connection, _ = setup
    connection.rows[QueryId.LEDGER][0]["count"] = 1
    with pytest.raises(ReadFailure, match="MIGRATION_PRECHECK_REFUSED"):
        migrator.execute_one(sign(grant), **args)
    assert not any("CREATE TABLE" in sql or sql == migrator.STAMP for sql, _ in connection.calls)


@pytest.mark.parametrize("material", ["backup_evidence", "release_manifest"])
def test_changed_review_material_never_connects(setup, material):
    grant, args, _, connections = setup
    args[material].write_text("{}", encoding="utf-8")
    with pytest.raises((ReadFailure, RuntimeError)):
        migrator.execute_one(sign(grant), **args)
    assert connections == []


def test_expired_evidence_never_connects(setup):
    grant, args, _, connections = setup
    args["clock"] = lambda: NOW + 61
    with pytest.raises(ReadFailure, match="EVIDENCE_INVALID_OR_EXPIRED"):
        migrator.execute_one(sign(grant), **args)
    assert connections == []


def test_inactive_binding_outside_active_count_blocks_ddl(setup):
    grant, args, connection, _ = setup
    connection.full_scope = dict.fromkeys(("count", "generic_frozen", "course_frozen"), 20)
    with pytest.raises(ReadFailure, match="MIGRATION_PRECHECK_REFUSED"):
        migrator.execute_one(sign(grant), **args)
    assert not any("CREATE TABLE" in sql or sql == migrator.STAMP for sql, _ in connection.calls)


def test_fixture_bundle_cannot_authorize_real_entry(setup):
    from credit_migration_test_support import cloud_bundle
    grant, args, _, connections = setup
    args["cloud_bundle"] = cloud_bundle(_Issuer(KEY, realm="fixture", clock=lambda: NOW))
    grant["cloud_bundle_fingerprint"] = args["cloud_bundle"]["bundle_fingerprint"]
    with pytest.raises(ReadFailure):
        migrator.execute_one(sign(grant), **args)
    assert connections == []


def test_tampered_approval_never_connects(setup):
    grant, args, _, connections = setup
    doc = sign(grant)
    doc["database_name"] = "another_database"
    with pytest.raises(ReadFailure, match="AUTHORIZATION_SIGNATURE_INVALID"):
        migrator.execute_one(doc, **args)
    assert connections == []


def test_no_missing_material_execute_override(capsys):
    assert migrator.main(["--execute-approved", "--migration", "0067"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "MIGRATION_EXECUTION_MATERIAL_REQUIRED"


def test_plan_does_not_initialize_app_database_settings():
    env = {**os.environ, "APP_ENV": "production", "DATABASE_URL": "invalid-synthetic-url"}
    result = subprocess.run([sys.executable, str(migrator.ROOT / "scripts" / "credit_single_migration.py"),
                             "--plan", "--migration", "0067"], capture_output=True, text=True, env=env, timeout=10)
    assert result.returncode == 0
    assert json.loads(result.stdout)["statement_count"] == 4
    assert "invalid-synthetic-url" not in result.stdout + result.stderr


@pytest.mark.parametrize("version", tuple(migrator.MIGRATIONS))
def test_exact_source_hash_and_script_plan(version):
    p, statements = migrator.plan(version)
    assert p["sha256"] == migrator.MIGRATIONS[version][1]
    assert p["statement_count"] == len(statements) > 0


def test_arbitrary_migration_rejected():
    with pytest.raises(ReadFailure, match="MIGRATION_NOT_ALLOWLISTED"):
        migrator.plan("0068")
