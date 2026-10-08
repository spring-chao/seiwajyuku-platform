"""Synthetic approval/evidence contracts for disposable migration tests only.

These keys and attestations are not production evidence or backup validation.
The real connection/scopes and source SQL are tested separately on isolated CI.
"""
import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import credit_single_migration as migrator
import r3_db_read as db
from r3_cloud_read import GATE_KEYS, SCOPE, SERVICE
from r3_read_evidence import SOURCES, _Issuer, fingerprint

AUTH_KEY = b"isolated-migration-authorizer-00000000000"
KEY = b"isolated-migration-evidence-0000000000000"
COMMIT = "c" * 40
REVISION = SERVICE + "-123"
NOW = 1000.0


def cloud_bundle(issuer):
    records = []
    payloads = {
        "ServiceEvidence": {"status": "normal", "traffic_type": "FLOW", "versions": [{"revision": REVISION, "percent": 100}]},
        "ReleaseEvidence": {"order_id": 123, "release_closed": True, "traffic_type": "FLOW", "params_empty": True},
        "RevisionEvidence": {"revision": REVISION, "status": "normal", "gates": dict.fromkeys(GATE_KEYS, "false")},
        "ManageTaskEvidence": {"task_id": 456, "task_inactive": True},
    }
    for _ in range(2):
        for kind, payload in payloads.items():
            subject = (SCOPE + "/" + REVISION if kind == "RevisionEvidence" else
                       SCOPE + "/release/123" if kind == "ReleaseEvidence" else
                       SCOPE + "/task/456" if kind == "ManageTaskEvidence" else SCOPE)
            records.append(issuer._issue(kind, next(iter(SOURCES[kind])), subject, payload, payload, NOW))
    flow_payload = {"stable_revision": REVISION}
    flow = issuer._issue("TrafficEvidence", next(iter(SOURCES["TrafficEvidence"])), SCOPE, flow_payload, flow_payload, NOW)
    records.append(flow)
    for source in sorted(SOURCES["RuntimeEvidence"]):
        payload = {"revision": REVISION, "http_status": 200, "identity_basis": "BRACKETED_FLOW_100", "route_proof": flow.raw_fingerprint}
        if source.endswith("build-info"):
            payload.update(commit_sha=COMMIT, environment="production")
        records.append(issuer._issue("RuntimeEvidence", source, SCOPE + "/" + REVISION, payload, payload, NOW))
    return issuer.bundle(SCOPE, REVISION, records, NOW)


def materials(tmp_path, rows, *, version="0067", database="fixture", principal="writer@host"):
    issuer = _Issuer(KEY, clock=lambda: NOW)
    cloud = cloud_bundle(issuer)
    payload = db.derive_baseline(rows)
    payload.update(connection_target_fingerprint=fingerprint({"scope": SCOPE, "database": database}),
                   query_results={q.value: fingerprint(rows[q]) for q in migrator.BUSINESS_QUERIES})
    record = issuer._issue("DatabaseBaselineEvidence", "mysql-readonly:R3_BASELINE", SCOPE, rows[db.QueryId.RULES], payload, NOW)
    bundle = issuer.bundle(SCOPE, REVISION, [record], NOW)
    manifest = {"schema_version": 1, "release_commit": COMMIT, "source_tree_sha": COMMIT,
                "ci_conclusion": "success", "github_ci_run": "isolated-ci", "build_id": "isolated-ci",
                "image_tag": "isolated-ci", "feature_flags": {}, "migration_state": {}}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    backup_path = tmp_path / "backup-proof.json"
    backup_path.write_bytes(b"isolated synthetic backup attestation contract")
    journal = migrator.MigrationJournal(tmp_path / "migrations.sqlite")
    p, _ = migrator.plan(version)
    grant = {
        "schema_version": 1, "purpose": "CREDIT_SINGLE_MYSQL_MIGRATION", "scope": SCOPE,
        "controller_commit": COMMIT, "approval_ref": "ISOLATED-MIGRATION-" + version,
        "approved_by": "isolated-reviewer", "realm": "live", "not_before": NOW - 1, "expires_at": NOW + 100,
        "database_name": database, "host": "127.0.0.1", "port": 3306, "principal": principal,
        "baseline_revision": REVISION, "journal_fingerprint": fingerprint(journal.path.resolve().as_posix()),
        "migration_version": version, "migration_sha256": p["sha256"], "migration_statement_count": p["statement_count"],
        "max_runs": 1, "cloud_bundle_fingerprint": cloud["bundle_fingerprint"], "db_bundle_fingerprint": bundle["bundle_fingerprint"],
        "runtime_commit": COMMIT, "backup_ref": "ISOLATED-BACKUP-001",
        "backup_evidence_sha256": hashlib.sha256(backup_path.read_bytes()).hexdigest(),
        "release_manifest_fingerprint": fingerprint(manifest),
        "expected_policy_binding_count": payload["active_binding_count"],
        "expected_course_reference_fill_count": 0,
    }
    args = dict(authorization_key=AUTH_KEY, collector_key=KEY, commit=COMMIT, version=version,
                cloud_bundle=cloud, db_bundle=bundle, release_manifest=manifest_path,
                backup_evidence=backup_path, journal=journal, clock=lambda: NOW)
    return grant, args


def sign(grant):
    return {**grant, "seal": _Issuer(AUTH_KEY)._mac(grant)}


def ready_rows(base):
    from app.services.course_credit_canonical import canonical_rule_rows, expected_persisted_rule
    rows = copy.deepcopy(base)
    rows[db.QueryId.VERSION][0]["status"] = "PUBLISHED"
    rows[db.QueryId.RULES] = []
    for index, rule in enumerate(canonical_rule_rows(), 1):
        projected = expected_persisted_rule(rule)
        projected["aliases_json"] = json.dumps(projected.pop("aliases"), ensure_ascii=False, separators=(",", ":"))
        rows[db.QueryId.RULES].append({"id": index, **projected})
    rows[db.QueryId.GENERIC_RULES] = [{**rows[db.QueryId.GENERIC_RULES][0], "rule_key": "GENERIC-" + str(i)} for i in range(6)]
    rows[db.QueryId.MIGRATION] = [{"version": migrator.MIGRATIONS[n][0]} for n in ("0064", "0065", "0066")]
    rows[db.QueryId.MAPPING] = [{"count": 1}]
    rows[db.QueryId.BINDING] = [{"count": 19, "generic_frozen": 19, "course_frozen": 19}]
    rows[db.QueryId.APPLY_AUDIT] = [{"count": 1}]
    return rows
