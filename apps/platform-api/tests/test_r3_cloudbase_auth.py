"""Offline CLI authentication tests: no Tencent request or production credential."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import r3_cloud_read as cloud
import r3_collect_readonly as collector
from r3_read_evidence import ReadFailure, _Issuer

RID = "00000000-0000-0000-0000-000000000000"
REVISION = cloud.SERVICE + "-260"


@pytest.fixture
def cli(monkeypatch):
    calls = []
    monkeypatch.setattr(cloud, "_cloudbase_command", lambda: ("node", "/trusted/bin/tcb"))
    monkeypatch.setenv("NODE_TLS_REJECT_UNAUTHORIZED", "0")
    monkeypatch.setenv("NODE_OPTIONS", "--require untrusted.js")

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout="CloudBase CLI 3.6.1\n" + json.dumps({"data": {"RequestId": RID}}),
            stderr="private diagnostic must not escape",
        )

    monkeypatch.setattr(cloud.subprocess, "run", run)
    return calls


def test_cli_ports_only_dispatch_five_fixed_reads(cli):
    ports = cloud._cli_ports()
    responses = [ports.service(), ports.revision(REVISION), ports.release(),
                 ports.task(123), ports.pods(REVISION)]
    assert ports.realm == "live"
    assert [args[4] for args, _ in cli] == [
        "DescribeCloudRunServerDetail", "DescribeVersionDetail", "DescribeReleaseOrder",
        "DescribeServerManageTask", "DescribeCloudRunPodList",
    ]
    assert all(json.loads(r.to_json_string())["RequestId"] == RID for r in responses)
    for args, opts in cli:
        params = json.loads(args[args.index("--body") + 1])
        assert params["EnvId"] == cloud.ENV_ID and params["ServerName"] == cloud.SERVICE
        assert set(params) <= {"EnvId", "ServerName", "VersionName", "TaskId", "PageSize", "PageNum"}
        assert args[0:4] == ["node", "/trusted/bin/tcb", "api", "tcbr"]
        assert args[-2:] == ["-r", cloud.REGION]
        assert opts.get("shell", False) is False
        assert opts["stdin"] == subprocess.DEVNULL
        assert opts["timeout"] == 10 and opts["capture_output"] is True
        assert opts["env"]["NODE_TLS_REJECT_UNAUTHORIZED"] == "1"
        assert "NODE_OPTIONS" not in opts["env"]
        assert opts["cwd"] == str(Path("/trusted/bin"))
    assert len(cli) == 5


@pytest.mark.parametrize("bad", ["other-service-260", "260;ReleaseGray", "260", None])
def test_cli_revision_rejected_before_dispatch(cli, bad):
    ports = cloud._cli_ports()
    with pytest.raises(ReadFailure, match="REVISION_ID_INVALID"):
        ports.revision(bad)
    with pytest.raises(ReadFailure, match="REVISION_ID_INVALID"):
        ports.pods(bad)
    assert cli == []


@pytest.mark.parametrize("bad", [0, -1, True, "123", None])
def test_cli_task_rejected_before_dispatch(cli, bad):
    with pytest.raises(ReadFailure, match="TASK_ID_INVALID"):
        cloud._cli_ports().task(bad)
    assert cli == []


@pytest.mark.parametrize("case", ["exit", "timeout", "malformed", "error", "trailing", "oversize"])
def test_cli_transport_fail_closed_without_raw_exception_or_retry(monkeypatch, cli, case, capsys):
    calls = []

    def run(*args, **kwargs):
        calls.append(1)
        if case == "timeout":
            raise subprocess.TimeoutExpired("credential-private", 10, output="secret-private")
        stdout = json.dumps({"data": {"RequestId": RID}})
        if case == "malformed":
            stdout = "credential-private"
        elif case == "error":
            stdout = json.dumps({"data": {"Error": {"Message": "credential-private"}}})
        elif case == "trailing":
            stdout += "\nsecret-private"
        elif case == "oversize":
            stdout = "a" * (16 * 1024 * 1024 + 1)
        return SimpleNamespace(returncode=1 if case == "exit" else 0,
                               stdout=stdout, stderr="secret-private")

    monkeypatch.setattr(cloud.subprocess, "run", run)
    with pytest.raises(ReadFailure) as caught:
        cloud._cli_ports().service()
    assert len(calls) == 1
    assert "private" not in str(caught.value) + capsys.readouterr().out


def test_cli_live_realm_cannot_issue_fixture_evidence(cli):
    issuer = _Issuer(b"isolated-fixture-key-000000000000", realm="fixture")
    with pytest.raises(ReadFailure, match="READ_CAPABILITIES_REQUIRED"):
        cloud.TencentCloudReadAdapter.authenticated_cli(issuer)
    assert cli == []


def test_cli_adapter_preserves_validation_and_redaction(cli, monkeypatch):
    def run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stderr="private-secret", stdout=json.dumps({
            "data": {
                "RequestId": RID,
                "BaseInfo": {"ServerName": cloud.SERVICE, "Status": "normal", "TrafficType": "FLOW"},
                "ServerConfig": {"EnvId": cloud.ENV_ID, "ServerName": cloud.SERVICE,
                                 "EnvParams": "private-secret"},
                "OnlineVersionInfos": [{"VersionName": REVISION, "FlowRatio": "100"}],
            },
        }))

    monkeypatch.setattr(cloud.subprocess, "run", run)
    issuer = _Issuer(b"isolated-live-test-key-00000000000")
    adapter = cloud.TencentCloudReadAdapter.authenticated_cli(issuer)
    record = adapter.service()
    assert record.payload()["versions"] == [{"revision": REVISION, "percent": 100,
                                            "image_reference": None, "image_digest": "UNKNOWN"}]
    assert "private-secret" not in json.dumps(record.payload())
    assert not any(hasattr(adapter, name) for name in ("release_gray", "execute", "write", "deploy"))


@pytest.mark.parametrize("case", ["missing", "wrong_name", "wrong_version", "no_entry"])
def test_cli_installation_must_be_official_pinned_package(tmp_path, monkeypatch, case):
    shim = tmp_path / "tcb.cmd"
    root = tmp_path / "node_modules" / "@cloudbase" / "cli"
    (root / "bin").mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({
        "name": "other" if case == "wrong_name" else "@cloudbase/cli",
        "version": "3.6.2" if case == "wrong_version" else "3.6.1",
    }), encoding="utf-8")
    if case != "no_entry":
        (root / "bin" / "tcb").write_text("// fixture", encoding="utf-8")
    monkeypatch.setattr(cloud.shutil, "which", lambda name: None if case == "missing" else
                        (str(shim) if name == "tcb" else "node"))
    with pytest.raises(ReadFailure, match="CLOUDBASE_CLI_"):
        cloud._cloudbase_command()


def test_cli_mode_still_requires_preexisting_trusted_key(tmp_path, monkeypatch, capsys):
    def no_provider(*args):
        pytest.fail("No authentication or cloud read before trusted-key checks")

    monkeypatch.setattr(cloud.TencentCloudReadAdapter, "authenticated_cli", no_provider)
    assert collector.main([
        "--collect-readonly-evidence", "--cloud-auth", "cloudbase-cli",
        "--baseline-revision", REVISION, "--runtime-commit", "a" * 40,
        "--manage-task-id", "123", "--output", str(tmp_path / "bundle.json"),
    ]) == 2
    assert "VERIFIER_KEY_REQUIRED" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_cli_mode_does_not_require_or_export_sdk_credentials(tmp_path, monkeypatch, capsys):
    for key in ("TENCENTCLOUD_SECRET_ID", "TENCENTCLOUD_SECRET_KEY", "TENCENTCLOUD_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(collector, "_load_verifier_key", lambda *args: b"isolated-fixture-key-000000000000")
    calls = []

    def provider(issuer):
        calls.append(issuer.realm)
        raise ReadFailure("TEST_STOP_BEFORE_NETWORK")

    monkeypatch.setattr(cloud.TencentCloudReadAdapter, "authenticated_cli", provider)
    assert collector.main([
        "--collect-readonly-evidence", "--cloud-auth", "cloudbase-cli",
        "--baseline-revision", REVISION, "--runtime-commit", "a" * 40,
        "--manage-task-id", "123", "--output", str(tmp_path / "bundle.json"),
    ]) == 2
    assert calls == ["live"]
    output = capsys.readouterr().out
    assert "TEST_STOP_BEFORE_NETWORK" in output and "CREDENTIALS_NOT_CONFIGURED" not in output
    assert not list(tmp_path.iterdir())
