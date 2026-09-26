import ast
import copy
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import r3_cloud_read as cloud
import r3_db_read as db
import r3_read_evidence as evidence
from r3_collect_readonly import main

NOW = 1000.0
KEY = b"isolated-test-collector-key-00000000"
REV = cloud.SERVICE + "-258"
OTHER = cloud.SERVICE + "-259"
COMMIT = "b" * 40
RID = "d9f8e51f-734d-4906-a406-b630a4285357"


class Response:
    def __init__(self, raw):
        self.raw = raw

    def to_json_string(self):
        return json.dumps(self.raw)


@pytest.fixture
def issuer():
    return evidence._Issuer(KEY, realm="fixture", clock=lambda: NOW)


@pytest.fixture
def raw():
    return {
        "service": {
            "RequestId": RID,
            "BaseInfo": {
                "ServerName": cloud.SERVICE,
                "Status": "normal",
                "TrafficType": "FLOW",
                "DefaultDomainName": "example.invalid",
            },
            "ServerConfig": {"EnvId": cloud.ENV_ID, "ServerName": cloud.SERVICE},
            "OnlineVersionInfos": [
                {
                    "VersionName": REV,
                    "FlowRatio": "100",
                    "ImageUrl": "registry.example/api@sha256:" + "a" * 64,
                }
            ],
        },
        "revision": {
            "RequestId": RID,
            "Name": REV,
            "Status": "normal",
            "EnvParams": json.dumps(
                {
                    **dict.fromkeys(cloud.GATE_KEYS, "false"),
                    "SECRET_KEY": "secret-value-must-not-leak",
                }
            ),
            "VpcConf": dict(
                zip(
                    cloud.VPC_KEYS,
                    ("vpc-id", "10.0.0.0/16", "subnet-id", "10.0.1.0/24"),
                )
            ),
            **dict.fromkeys(cloud.CONFIG_KEYS, "synthetic"),
        },
        "release": {
            "RequestId": RID,
            "IsExist": True,
            "ReleaseOrderInfo": {
                "Id": 123,
                "ServerName": cloud.SERVICE,
                "IsReleasing": False,
                "ReleaseStatus": "finished",
                "TrafficType": "FLOW",
                "TrafficTypeValues": [],
                "CurrentVersion": {"UrlParam": None},
                "ReleaseVersion": {"UrlParam": None},
            },
        },
        "task": {
            "RequestId": RID,
            "IsExist": True,
            "Task": {
                "Id": 456,
                "EnvId": cloud.ENV_ID,
                "ServerName": cloud.SERVICE,
                "Status": "finished",
                "ReleaseId": 123,
                "VersionName": REV,
            },
        },
        "pods": {
            "RequestId": RID,
            "PodList": [
                {
                    "PodId": "pod-1",
                    "Status": "Running",
                    "Webshell": "https://secret.invalid/?token=sensitive",
                }
            ],
            "TotalCount": 1,
        },
    }


def adapter(issuer, raw):
    return cloud.TencentCloudReadAdapter(
        issuer,
        cloud._ReadPorts(
            lambda: Response(raw["service"]),
            lambda name: Response(raw["revision"]),
            lambda: Response(raw["release"]),
            lambda task: Response(raw["task"]),
            lambda name: Response(raw["pods"]),
        ),
    )


@pytest.fixture
def runtime(monkeypatch):
    calls = []

    def get(self, path):
        calls.append(path)
        if path == cloud.PATHS[0]:
            return {
                "commit_sha": COMMIT,
                "environment": "production",
                "image_digest": "unknown",
            }, RID
        return {"service": cloud.SERVICE, "status": "ok"}, RID

    monkeypatch.setattr(cloud._GetOnly, "get", get)
    return calls


def test_bundle_roundtrip_and_no_state_transition(issuer, raw, runtime, tmp_path):
    bundle = cloud.collect_cloud_runtime(adapter(issuer, raw), issuer, REV, 456, COMMIT)
    records = evidence.verify_bundle(
        bundle,
        key=KEY,
        expected_scope=cloud.SCOPE,
        expected_revision=REV,
        now=NOW,
        expected_realm="fixture",
    )
    assert isinstance(records[0], evidence.ServiceEvidence)
    assert len(runtime) == 3
    assert len(records) == 13
    assert bundle["production_ready"] is False
    assert "secret-value-must-not-leak" not in json.dumps(bundle)
    assert "sensitive" not in json.dumps(bundle)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "production"),
        ("subject", "wrong"),
        ("payload_json", "{}"),
        ("raw_fingerprint", "f" * 64),
        ("observed_at", NOW + 1),
    ],
)
def test_forged_record_rejected(issuer, raw, field, value):
    record = adapter(issuer, raw).service()
    changed = replace(record, **{field: value})
    if field == "observed_at":
        with pytest.raises(evidence.ReadFailure):
            issuer.bundle(cloud.SCOPE, REV, [changed], NOW)
        return
    bundle = issuer.bundle(cloud.SCOPE, REV, [changed], NOW)
    with pytest.raises(evidence.ReadFailure, match="EVIDENCE_TAMPERED"):
        evidence.verify_bundle(
            bundle,
            key=KEY,
            expected_scope=cloud.SCOPE,
            expected_revision=REV,
            now=NOW,
            expected_realm="fixture",
        )


@pytest.mark.parametrize("now", [NOW - 1, NOW + 60, NOW + 900])
def test_time_validation(issuer, raw, now):
    bundle = issuer.bundle(cloud.SCOPE, REV, [adapter(issuer, raw).service()], NOW)
    with pytest.raises(evidence.ReadFailure):
        evidence.verify_bundle(
            bundle,
            key=KEY,
            expected_scope=cloud.SCOPE,
            expected_revision=REV,
            now=now,
            expected_realm="fixture",
        )


@pytest.mark.parametrize("change", ["scope", "revision", "fingerprint", "realm"])
def test_bundle_tamper_or_wrong_expected_identity(issuer, raw, change):
    bundle = issuer.bundle(cloud.SCOPE, REV, [adapter(issuer, raw).service()], NOW)
    if change == "fingerprint":
        bundle["bundle_fingerprint"] = "f" * 64
    with pytest.raises(evidence.ReadFailure):
        evidence.verify_bundle(
            bundle,
            key=KEY,
            expected_scope="wrong" if change == "scope" else cloud.SCOPE,
            expected_revision=OTHER if change == "revision" else REV,
            expected_realm="live" if change == "realm" else "fixture",
            now=NOW,
        )


def test_fixture_port_not_accepted_as_live(raw):
    with pytest.raises(evidence.ReadFailure):
        adapter(evidence._Issuer(KEY), raw)


@pytest.mark.parametrize("rid", [None, "", "token=private", 123])
def test_request_id_required(issuer, raw, rid):
    raw["service"]["RequestId"] = rid
    with pytest.raises(evidence.ReadFailure, match="REQUEST_ID_INVALID"):
        adapter(issuer, raw).service()


@pytest.mark.parametrize(
    "part,field,value,method,args",
    [
        (
            "service",
            "BaseInfo",
            {"ServerName": "wrong", "Status": "normal", "TrafficType": "FLOW"},
            "service",
            (),
        ),
        ("revision", "Name", OTHER, "revision", (REV,)),
        ("pods", "TotalCount", 100, "pods", (REV,)),
        (
            "task",
            "Task",
            {
                "Id": 457,
                "EnvId": cloud.ENV_ID,
                "ServerName": cloud.SERVICE,
                "Status": "finished",
                "ReleaseId": 123,
                "VersionName": REV,
            },
            "task",
            (456,),
        ),
    ],
)
def test_subject_and_incomplete_response(issuer, raw, part, field, value, method, args):
    raw[part][field] = value
    with pytest.raises(evidence.ReadFailure):
        getattr(adapter(issuer, raw), method)(*args)


@pytest.mark.parametrize(
    "part,field,value",
    [
        ("release", "IsReleasing", True),
        ("release", "ReleaseStatus", "gray"),
        (
            "release",
            "TrafficTypeValues",
            [{"Key": "sj_canary", "Value": "do-not-leak"}],
        ),
        ("task", "Status", "running"),
        ("task", "ReleaseId", 124),
    ],
)
def test_active_state_blocks_runtime(issuer, raw, runtime, part, field, value):
    raw[part]["ReleaseOrderInfo" if part == "release" else "Task"][field] = value
    with pytest.raises(evidence.ReadFailure):
        cloud.collect_cloud_runtime(adapter(issuer, raw), issuer, REV, 456, COMMIT)
    assert runtime == []


def test_traffic_changes_mid_collection(issuer, raw, monkeypatch, runtime):
    a = adapter(issuer, raw)
    original = a.service
    count = 0

    def service():
        nonlocal count
        count += 1
        if count > 1:
            raw["service"]["BaseInfo"]["TrafficType"] = "URL_PARAMS"
        return original()

    monkeypatch.setattr(a, "service", service)
    with pytest.raises(evidence.ReadFailure, match="SNAPSHOT_CHANGED"):
        cloud.collect_cloud_runtime(a, issuer, REV, 456, COMMIT)


def test_snapshot_span(issuer, raw):
    record = adapter(issuer, raw).service()
    issuer.clock = lambda: NOW + 31
    with pytest.raises(evidence.ReadFailure, match="SNAPSHOT_NOT_COHERENT"):
        issuer.bundle(cloud.SCOPE, REV, [record], NOW)


def test_config_unknown_and_mismatches(issuer, raw):
    a = adapter(issuer, raw)
    stable = a.revision(REV)
    image = a.service().payload()["versions"][0]
    assert cloud.compare_config(stable, stable, image, image)["other_config_equal"]
    raw["revision"]["VpcConf"]["SubnetId"] = "different"
    changed = a.revision(REV)
    assert (
        cloud.compare_config(stable, changed, image, image)["vpc.SubnetId"]
        == "DIFFERENT"
    )
    raw["revision"]["EnvParams"] = json.dumps(dict.fromkeys(cloud.GATE_KEYS, "true"))
    changed = a.revision(REV)
    assert not changed.payload()["stable_false_gates_verified"]
    assert not cloud.compare_config(stable, changed, image, image)["other_config_equal"]
    unknown = {**image, "image_digest": "UNKNOWN"}
    assert (
        cloud.compare_config(stable, stable, image, unknown)["image_digest"]
        == "UNKNOWN"
    )
    assert not cloud.compare_config(stable, stable, image, unknown)[
        "other_config_equal"
    ]
    assert (
        cloud.compare_config(
            stable, stable, image, {**image, "image_digest": "b" * 64}
        )["image_digest"]
        == "DIFFERENT"
    )


def test_runtime_commit_drift(issuer, runtime):
    with pytest.raises(evidence.ReadFailure, match="RUNTIME_COMMIT"):
        cloud.RuntimeReadAdapter(issuer, "https://example.invalid").collect(
            REV, "c" * 40
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid",
        "https://u:p@example.invalid",
        "https://example.invalid/write",
        "https://example.invalid?token=x",
    ],
)
def test_http_origin_rejects(url):
    with pytest.raises(evidence.ReadFailure):
        cloud._GetOnly(url)


def test_http_never_writes_or_redirects(monkeypatch):
    requests = []

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, **kwargs):
            requests.append((method, path))

        def getresponse(self):
            raise TimeoutError("SECRET_DONT_LOG")

        def close(self):
            pass

    monkeypatch.setattr(cloud.http.client, "HTTPSConnection", Connection)
    transport = cloud._GetOnly("https://example.invalid")
    with pytest.raises(evidence.ReadFailure, match="HTTP_READ_FAILED") as exc:
        transport.get(cloud.PATHS[0])
    assert "SECRET" not in str(exc.value)
    assert requests == [("GET", cloud.PATHS[0])]
    for path in ("/production-actions", "/settle", "/reverse", "/historical-import"):
        with pytest.raises(evidence.ReadFailure):
            transport.get(path)
    assert len(requests) == 1


def test_sdk_exception_is_safe_and_attempt_once(issuer):
    calls = []

    def fail():
        calls.append(1)
        raise TimeoutError("SecretKey=do-not-log")

    a = cloud.TencentCloudReadAdapter(
        issuer, cloud._ReadPorts(fail, fail, fail, fail, fail)
    )
    with pytest.raises(evidence.ReadFailure, match="CLOUD_READ_FAILED") as caught:
        a.service()
    assert caught.value.attempt_count == 1 and calls == [1]
    assert "do-not-log" not in str(caught.value)


def test_fixed_sdk_read_methods_exist_and_no_writes():
    from tencentcloud.tcbr.v20220217.tcbr_client import TcbrClient

    tree = ast.parse(Path(cloud.__file__).read_text(encoding="utf-8"))
    methods = {
        n.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "client"
    }
    assert methods == {
        "DescribeCloudRunServerDetail",
        "DescribeVersionDetail",
        "DescribeReleaseOrder",
        "DescribeServerManageTask",
        "DescribeCloudRunPodList",
    }
    assert all(callable(getattr(TcbrClient, x)) for x in methods)
    for method in (
        "UpdateCloudRunServer",
        "ReleaseGray",
        "SubmitServerConfigChangeDiff",
        "StartVersionInstance",
        "StopVersionInstance",
        "OperateServerManage",
        "DeleteCloudRunVersions",
        "invoke",
        "call_api",
        "raw_generic_request",
    ):
        assert not hasattr(cloud.TencentCloudReadAdapter, method)
        assert method not in methods
    assert "NoopRetryer()" in Path(cloud.__file__).read_text(encoding="utf-8")


def test_cli_no_production_options_and_missing_credentials(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.delenv("TENCENTCLOUD_SECRET_ID", raising=False)
    args = [
        "--collect-readonly-evidence",
        "--baseline-revision",
        REV,
        "--runtime-commit",
        COMMIT,
        "--manage-task-id",
        "456",
        "--output",
        str(tmp_path / "bundle.json"),
    ]
    assert main(args) == 2
    assert "NOT_RUN" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())
    with pytest.raises(SystemExit):
        main(args + ["--execute-production"])


@pytest.fixture
def db_rows():
    return {
        db.QueryId.ACCESS: [
            {
                "database_name": "fixture",
                "principal": "readonly@host",
                "roles": "NONE",
                "read_only": 1,
            }
        ],
        db.QueryId.PRIVILEGES: [{"privilege_type": "SELECT"}],
        db.QueryId.VERSION: [
            {
                "id": 1,
                "plan_key": "STANDARD_3Y_2026",
                "version_label": "2026.1",
                "status": "DRAFT",
                "based_on_version_label": None,
            }
        ],
        db.QueryId.RULES: [
            {
                "id": 1,
                "course_key": "fixture",
                "course_name": "fixture",
                "year_index": 1,
                "credit_points": 0,
                "status": "PENDING",
                "source": "fixture",
                "aliases_json": "[]",
            }
        ],
        db.QueryId.GENERIC_VERSION: [{"id": 1, "status": "PUBLISHED"}],
        db.QueryId.GENERIC_RULES: [
            {
                "rule_key": "fixture",
                "settlement_model": "DAILY_ONCE",
                "points": "1",
                "cap_points": None,
                "status": "ACTIVE",
            }
        ],
        db.QueryId.MAPPING: [{"count": 0}],
        db.QueryId.BINDING: [{"count": 19, "generic_frozen": 0, "course_frozen": 0}],
        db.QueryId.LEDGER: [{"count": 0, "points": "0.00"}],
        db.QueryId.MIGRATION: [],
        db.QueryId.APPLY_AUDIT: [{"count": 0}],
        db.QueryId.PLACEHOLDER: [{"count": 0}],
    }


class DbFixture:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def read(self, query_id):
        self.calls.append(query_id)
        return copy.deepcopy(self.rows[query_id])


def test_db_derived_fields_and_drift(issuer, db_rows):
    a = db.DatabaseReadAdapter(
        issuer, DbFixture(db_rows), scope="fixture/db", database_name="fixture"
    )
    first = a.collect_fixture()
    assert isinstance(first, evidence.DatabaseBaselineEvidence)
    assert first.payload()["active_binding_count"] == 19
    assert "invariants_verified" not in first.payload()
    db_rows[db.QueryId.RULES][0]["credit_points"] = 2
    assert (
        a.collect_fixture().payload()["production_fingerprint"]
        != first.payload()["production_fingerprint"]
    )


def test_db_authorization_blocks_all_reads(issuer, db_rows):
    port = DbFixture(db_rows)
    a = db.DatabaseReadAdapter(
        issuer, port, scope="fixture/db", database_name="fixture"
    )
    with pytest.raises(evidence.ReadFailure, match="NOT_AUTHORIZED"):
        a.collect()
    assert port.calls == []


@pytest.mark.parametrize(
    "query,field,value",
    [
        (db.QueryId.ACCESS, "read_only", 0),
        (db.QueryId.ACCESS, "roles", "admin"),
        (db.QueryId.PRIVILEGES, "privilege_type", "UPDATE"),
    ],
)
def test_db_not_readonly_rejected(issuer, db_rows, query, field, value):
    db_rows[query][0][field] = value
    with pytest.raises(evidence.ReadFailure, match="READONLY"):
        db.DatabaseReadAdapter(
            issuer, DbFixture(db_rows), scope="fixture/db", database_name="fixture"
        ).collect_fixture()


def test_db_missing_fields(db_rows):
    del db_rows[db.QueryId.RULES][0]["aliases_json"]
    with pytest.raises(evidence.ReadFailure, match="FIELDS_MISSING"):
        db.derive_baseline(db_rows)


def test_sql_allowlist_no_arbitrary_sql():
    import re

    for sql in db.SQL.values():
        assert sql.startswith("SELECT ") and ";" not in sql
        assert not re.search(
            r"\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|CALL|SET|GET_LOCK)\b", sql
        )
    with pytest.raises(evidence.ReadFailure, match="QUERY_NOT_ALLOWED"):
        db.MysqlSelectPort(None).read("SELECT 1")
    assert not hasattr(db.MysqlSelectPort, "execute")
    assert not hasattr(db.MysqlSelectPort, "query")


def test_phase3_never_imports_journal_or_production_writers():
    scripts = Path(cloud.__file__).parent
    for name in (
        "r3_cloud_read.py",
        "r3_db_read.py",
        "r3_read_evidence.py",
        "r3_collect_readonly.py",
    ):
        tree = ast.parse((scripts / name).read_text(encoding="utf-8"))
        imports = [x.module for x in ast.walk(tree) if isinstance(x, ast.ImportFrom)]
        assert not any(
            x
            and any(
                b in x
                for b in (
                    "deploy_cloudrun",
                    "production_operations",
                    "r3_envelope_journal",
                    "r3_execution_envelope",
                    "app.db",
                )
            )
            for x in imports
        )


def test_redaction_before_hash(issuer):
    raw = {
        "SecretKey": "NOPE",
        "Token": "NOPE",
        "Authorization": "Bearer NOPE",
        "nested": {"DATABASE_URL": "mysql://u:p@host"},
        "EnvParams": "NOPE",
    }
    text = json.dumps(evidence.redact(raw))
    assert "NOPE" not in text and "u:p" not in text
    record = issuer._issue(
        "ServiceEvidence",
        "tencent:DescribeCloudRunServerDetail",
        cloud.SCOPE,
        raw,
        {"status": "normal"},
        NOW,
        RID,
    )
    assert "NOPE" not in json.dumps(asdict(record))


def test_db_state_changes_reject_entire_snapshot(issuer, db_rows):
    class Changing(DbFixture):
        def read(self, query_id):
            if query_id == db.QueryId.LEDGER and query_id in self.calls:
                self.rows[query_id] = [{"count": 1, "points": "1.00"}]
            return super().read(query_id)

    with pytest.raises(evidence.ReadFailure, match="SNAPSHOT_CHANGED"):
        db.DatabaseReadAdapter(
            issuer, Changing(db_rows), scope="fixture/db", database_name="fixture"
        ).collect_fixture()


def test_mysql_port_only_sends_whitelisted_selects(db_rows):
    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql):
            statements.append(sql)

        def fetchmany(self, size):
            assert size == 501
            return db_rows[db.QueryId.LEDGER]

    class Connection:
        def cursor(self):
            return Cursor()

    assert (
        db.MysqlSelectPort(Connection()).read(db.QueryId.LEDGER)
        == db_rows[db.QueryId.LEDGER]
    )
    assert statements == [db.SQL[db.QueryId.LEDGER]]


def test_live_db_cannot_use_fixture_collection(db_rows):
    port = DbFixture(db_rows)
    a = db.DatabaseReadAdapter(
        evidence._Issuer(KEY), port, scope="prod/db", database_name="production"
    )
    with pytest.raises(evidence.ReadFailure, match="NOT_AUTHORIZED"):
        a.collect_fixture()
    assert port.calls == []


def test_runtime_alone_cannot_be_bundle_proof(issuer, runtime):
    records = cloud.RuntimeReadAdapter(issuer, "https://example.invalid").collect(
        REV, COMMIT
    )
    bundle = issuer.bundle(cloud.SCOPE, REV, records, NOW)
    with pytest.raises(evidence.ReadFailure, match="RUNTIME_IDENTITY_NOT_PROVEN"):
        evidence.verify_bundle(
            bundle,
            key=KEY,
            expected_scope=cloud.SCOPE,
            expected_revision=REV,
            now=NOW,
            expected_realm="fixture",
        )


def test_sdk_request_id_and_error_metadata(issuer):
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
        TencentCloudSDKException,
    )

    def fail():
        raise TencentCloudSDKException(
            "ResourceUnavailable", "Authorization=NEVER_EXPORT", RID
        )

    a = cloud.TencentCloudReadAdapter(
        issuer, cloud._ReadPorts(fail, fail, fail, fail, fail)
    )
    with pytest.raises(evidence.ReadFailure) as caught:
        a.service()
    assert caught.value.error_code == "ResourceUnavailable"
    assert caught.value.request_id == RID
    assert "NEVER_EXPORT" not in str(caught.value)


def test_fingerprint_algorithm_matches_existing_contract(db_rows):
    from app.services.course_credit_reconciliation import production_fingerprint

    result = db.derive_baseline(db_rows)
    assert result["production_fingerprint"] == production_fingerprint(
        db_rows[db.QueryId.VERSION][0], db_rows[db.QueryId.RULES]
    )


def test_subject_binding_even_for_internal_signed_record(issuer, raw):
    original = adapter(issuer, raw).service()
    signed_wrong = issuer._issue(
        "ServiceEvidence",
        original.source,
        "wrong-service",
        {},
        original.payload(),
        NOW,
        RID,
    )
    bundle = issuer.bundle(cloud.SCOPE, REV, [signed_wrong], NOW)
    with pytest.raises(evidence.ReadFailure, match="SUBJECT_MISMATCH"):
        evidence.verify_bundle(
            bundle,
            key=KEY,
            expected_scope=cloud.SCOPE,
            expected_revision=REV,
            now=NOW,
            expected_realm="fixture",
        )
