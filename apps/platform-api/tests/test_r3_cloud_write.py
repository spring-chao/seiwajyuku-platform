"""Offline fixed SDK requests and durable budgets; no real cloud writes."""
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import r3_cloud_write as writer
from r3_read_evidence import ReadFailure, _Issuer, fingerprint

AUTH_KEY = b"synthetic-approval-key-for-tests-only-0001"
BINDING_KEY = b"synthetic-request-key-for-tests-only-0002"
COMMIT = "c" * 40
NOW = 1000
STABLE = writer.SERVICE + "-300"
CANDIDATE = writer.SERVICE + "-301"
TOKEN = "synthetic_route_token_" + "a" * 32
ENV = {**writer.BEFORE_GATES, "DATABASE_URL": "synthetic-private-db-value", "JWT_SECRET": "synthetic-private-jwt-value"}
VPC = {"VpcId": "vpc-test1", "VpcCIDR": "10.0.0.0/16", "SubnetId": "subnet-test1", "SubnetCIDR": "10.0.1.0/24"}
RID = "00000000-0000-0000-0000-000000000001"


def plans():
    return [writer.enable_candidate_plan(STABLE, image_url="registry.invalid/app@sha256:" + "a" * 64, stable_env=ENV, version_vpc=VPC),
            writer.target_candidate_plan(STABLE, CANDIDATE, TOKEN), writer.restore_stable_plan(STABLE, CANDIDATE)]


def grant(plan, journal):
    return {"schema_version": 1, "purpose": "R3_CLOUD_WRITE_CAPABILITY", "scope": writer.SCOPE,
        "controller_commit": COMMIT, "approval_ref": "SYNTHETIC-" + plan.action,
        "approved_by": "synthetic-reviewer", "realm": "live", "not_before": NOW - 1,
        "expires_at": NOW + 60, "operation_id": "SYNTHETIC-OPERATION-1", "action": plan.action,
        "request_fingerprint": plan.binding(BINDING_KEY),
        "journal_fingerprint": fingerprint(journal.path.resolve().as_posix()), "max_attempts": 1}


def signed(data):
    return {**data, "seal": _Issuer(AUTH_KEY)._mac(data)}


@pytest.fixture
def sdk(monkeypatch):
    import tencentcloud.tcbr.v20220217.tcbr_client as module
    calls, profiles, behavior = [], [], {"fail": False, "invalid": False}
    class Client:
        def __init__(self, credential, region, profile):
            profiles.append((credential, region, profile))
        def response(self, action, request):
            calls.append((action, json.loads(request.to_json_string())))
            if behavior["fail"]:
                raise TimeoutError("secret-id private-token secret-url must not escape")
            body = {"RequestId": RID}
            if action == "UpdateCloudRunServer":
                body["TaskId"] = True if behavior["invalid"] else 701
            elif behavior["invalid"]:
                body["RequestId"] = "invalid?secret=private-token"
            return SimpleNamespace(to_json_string=lambda: json.dumps(body))
        def UpdateCloudRunServer(self, request):
            return self.response("UpdateCloudRunServer", request)
        def ReleaseGray(self, request):
            return self.response("ReleaseGray", request)
    monkeypatch.setattr(module, "TcbrClient", Client)
    transport = writer.CloudWriteTransport.authenticated("synthetic-id", "synthetic-key", "synthetic-token")
    return transport, calls, profiles, behavior


def invoke(transport, plan, doc, journal, **overrides):
    method = {writer.CREATE: transport.create_enable_candidate,
        writer.TARGET: transport.target_candidate, writer.RESTORE: transport.restore_stable_flow}[plan.action]
    kwargs = dict(authorization_key=AUTH_KEY, binding_key=BINDING_KEY,
                  commit=COMMIT, journal=journal, clock=lambda: NOW)
    kwargs.update(overrides)
    return method(plan, doc, **kwargs)


def test_exact_typed_sdk_requests_only_gray_target_and_zero_flow(sdk, tmp_path):
    from tencentcloud.common.retry import NoopRetryer
    transport, calls, profiles, _ = sdk
    journal = writer.WriteAttemptJournal(tmp_path / "attempts.sqlite")
    for plan in plans():
        result = invoke(transport, plan, signed(grant(plan, journal)), journal)
        assert result["result"] == "RESPONSE_RECEIVED" and result["replay_allowed"] is False
        assert result["lifecycle_verified"] is False and result["production_ready"] is False
    assert [name for name, _ in calls] == ["UpdateCloudRunServer", "ReleaseGray", "ReleaseGray"]
    create, target, restore = [body for _, body in calls]
    assert all(b["EnvId"] == writer.ENV_ID and b["ServerName"] == writer.SERVICE for b in (create, target, restore))
    assert create["DeployInfo"]["ReleaseType"] == "GRAY" and create["DeployInfo"]["DeployType"] == "image"
    assert [i["Key"] for i in create["Items"]] == ["VpcConf", "EnvParam"]
    assert create["Items"][0]["VpcConf"] == VPC
    assert json.loads(create["Items"][1]["Value"]) == {**ENV, writer.G5: "true"}
    assert target["TrafficType"] == "URL_PARAMS" and restore["TrafficType"] == "FLOW"
    for body in (target, restore):
        assert body["GrayFlowRatio"] == 0
        assert [(v["VersionName"], v["FlowRatio"]) for v in body["VersionFlowItems"]] == [(STABLE, 100), (CANDIDATE, 0)]
    assert restore["VersionFlowItems"][1].get("UrlParam") is None
    assert "CloseGrayRelease" not in restore
    _, region, profile = profiles[0]
    assert region == writer.REGION and isinstance(profile.retryer, NoopRetryer)
    assert profile.httpProfile.endpoint == "tcbr.tencentcloudapi.com" and profile.httpProfile.reqTimeout == 10
    assert not hasattr(transport, "invoke") and not hasattr(transport, "_tcbr")


@pytest.mark.parametrize("index", [0, 1, 2])
def test_restart_and_new_approval_cannot_replay_same_operation_action(sdk, tmp_path, index):
    transport, calls, _, _ = sdk
    plan = plans()[index]
    journal = writer.WriteAttemptJournal(tmp_path / "attempts.sqlite")
    data = grant(plan, journal)
    invoke(transport, plan, signed(data), journal)
    reloaded = writer.WriteAttemptJournal(journal.path)
    for approval in (data["approval_ref"], "SYNTHETIC-NEW-APPROVAL"):
        with pytest.raises(ReadFailure, match="R3_WRITE_REPLAY_DISALLOWED"):
            invoke(transport, plan, signed({**data, "approval_ref": approval}), reloaded)
    assert len(calls) == 1


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("failure", ["fail", "invalid"])
def test_unknown_response_spends_budget_without_secret_or_retry(sdk, tmp_path, capsys, index, failure):
    transport, calls, _, behavior = sdk
    behavior[failure] = True
    plan = plans()[index]
    journal = writer.WriteAttemptJournal(tmp_path / "attempts.sqlite")
    doc = signed(grant(plan, journal))
    with pytest.raises(ReadFailure, match="R3_CLOUD_WRITE_OUTCOME_UNKNOWN") as exc:
        invoke(transport, plan, doc, journal)
    assert exc.value.__cause__ is None and "private" not in str(exc.value)
    with pytest.raises(ReadFailure, match="R3_WRITE_REPLAY_DISALLOWED"):
        invoke(transport, plan, doc, writer.WriteAttemptJournal(journal.path))
    assert len(calls) == 1 and not capsys.readouterr().out
    with sqlite3.connect(journal.path) as db:
        assert db.execute("SELECT outcome FROM write_attempts").fetchall() == [("UNKNOWN",)]


def test_local_record_failure_after_cloud_success_is_unknown(sdk, tmp_path, monkeypatch):
    transport, calls, _, _ = sdk
    plan = plans()[0]
    journal = writer.WriteAttemptJournal(tmp_path / "attempts.sqlite")
    doc = signed(grant(plan, journal))
    monkeypatch.setattr(journal, "response_received", lambda *args: (_ for _ in ()).throw(OSError("private-disk-error")))
    with pytest.raises(ReadFailure, match="R3_CLOUD_WRITE_OUTCOME_UNKNOWN"):
        invoke(transport, plan, doc, journal)
    with pytest.raises(ReadFailure, match="R3_WRITE_REPLAY_DISALLOWED"):
        invoke(transport, plan, doc, writer.WriteAttemptJournal(journal.path))
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"max_attempts": 2}, {"max_attempts": True}, {"scope": "different-service"},
    {"controller_commit": "d" * 40}, {"realm": "fixture"}, {"expires_at": NOW},
    {"action": writer.RESTORE}, {"purpose": "R3_DB_BASELINE_READ"}, {"operation_id": True},
    {"request_fingerprint": "a" * 64}, {"journal_fingerprint": "b" * 64}, {"extra": "value"},
])
def test_changed_or_widened_approval_never_dispatches(sdk, tmp_path, changes):
    transport, calls, _, _ = sdk
    plan = plans()[0]
    journal = writer.WriteAttemptJournal(tmp_path / "attempts.sqlite")
    with pytest.raises(ReadFailure):
        invoke(transport, plan, signed({**grant(plan, journal), **changes}), journal)
    assert calls == []


def test_signature_and_independent_key_required(sdk, tmp_path):
    transport, calls, _, _ = sdk
    plan = plans()[0]
    journal = writer.WriteAttemptJournal(tmp_path / "attempts.sqlite")
    doc = signed(grant(plan, journal))
    doc["max_attempts"] = 2
    with pytest.raises(ReadFailure, match="AUTHORIZATION_SIGNATURE_INVALID"):
        invoke(transport, plan, doc, journal)
    data = {**grant(plan, journal), "request_fingerprint": plan.binding(AUTH_KEY)}
    with pytest.raises(ReadFailure, match="R3_WRITE_INDEPENDENT_APPROVAL_REQUIRED"):
        invoke(transport, plan, signed(data), journal, binding_key=AUTH_KEY)
    assert calls == []


@pytest.mark.parametrize("case", ["full", "source", "env", "service", "vpc_extra", "target_percent", "restore_token"])
def test_forged_plan_cannot_expand_actions_even_with_signed_binding(sdk, tmp_path, case):
    transport, calls, _, _ = sdk
    plan = plans()[1 if case == "target_percent" else 2 if case == "restore_token" else 0]
    body = json.loads(plan.request_json)
    if case == "full":
        body["DeployInfo"]["ReleaseType"] = "FULL"
    elif case == "source":
        body["DeployInfo"]["DeployType"] = "package"
    elif case == "env":
        env = json.loads(body["Items"][1]["Value"])
        env["LEARNING_CREDIT_SETTLEMENT_ENABLED"] = "true"
        body["Items"][1]["Value"] = json.dumps(env)
    elif case == "service":
        body["ServerName"] = "another-service"
    elif case == "vpc_extra":
        body["Items"][0]["VpcConf"]["Override"] = "value"
    elif case == "target_percent":
        body["VersionFlowItems"][1]["FlowRatio"] = 1
    elif case == "restore_token":
        body["VersionFlowItems"][1]["UrlParam"] = {"Key": "token", "Value": TOKEN}
    plan = replace(plan, request_json=json.dumps(body))
    journal = writer.WriteAttemptJournal(tmp_path / "attempts.sqlite")
    with pytest.raises(ReadFailure):
        invoke(transport, plan, signed(grant(plan, journal)), journal)
    assert calls == []


@pytest.mark.parametrize("env_change", [{"APP_ENV": "test"}, {writer.G5: "true"}, {"RUN_BOOTSTRAP_ON_STARTUP": "true"}, {"LEARNING_CREDIT_SETTLEMENT_ENABLED": "true"}])
def test_plan_rejects_wrong_preexisting_gates(env_change):
    with pytest.raises(ReadFailure, match="R3_STABLE_GATES_NOT_VERIFIED"):
        writer.enable_candidate_plan(STABLE, image_url="registry.invalid/app@sha256:" + "a" * 64, stable_env={**ENV, **env_change}, version_vpc=VPC)


@pytest.mark.parametrize("image", ["registry.invalid/app:latest", "registry.invalid/app@sha256:short", "https://registry.invalid/app@sha256:" + "a" * 64])
def test_mutable_or_malformed_images_never_plan(image):
    with pytest.raises(ReadFailure, match="R3_IMMUTABLE_IMAGE_REQUIRED"):
        writer.enable_candidate_plan(STABLE, image_url=image, stable_env=ENV, version_vpc=VPC)


def test_binding_covers_secrets_and_summary_journal_do_not_contain_them(sdk, tmp_path):
    transport, _, _, _ = sdk
    first = plans()[0]
    other = writer.enable_candidate_plan(STABLE, image_url="registry.invalid/app@sha256:" + "a" * 64, stable_env={**ENV, "JWT_SECRET": "changed-private-value"}, version_vpc=VPC)
    assert first.binding(BINDING_KEY) != other.binding(BINDING_KEY)
    journal = writer.WriteAttemptJournal(tmp_path / "attempts.sqlite")
    result = invoke(transport, first, signed(grant(first, journal)), journal)
    rendered = repr(first) + json.dumps(first.summary()) + json.dumps(result)
    for private in (ENV["DATABASE_URL"], ENV["JWT_SECRET"], TOKEN):
        assert private not in rendered and private.encode() not in journal.path.read_bytes()


def test_concurrent_reservations_send_only_once(sdk, tmp_path):
    transport, calls, _, _ = sdk
    plan = plans()[0]
    journal = writer.WriteAttemptJournal(tmp_path / "attempts.sqlite")
    doc = signed(grant(plan, journal))
    def attempt(_):
        try:
            return invoke(transport, plan, doc, journal)["result"]
        except ReadFailure as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(8)))
    assert results.count("RESPONSE_RECEIVED") == 1 and results.count("R3_WRITE_REPLAY_DISALLOWED") == 7
    assert len(calls) == 1


def test_plan_expiring_after_reservation_never_dispatches(sdk, tmp_path):
    transport, calls, _, _ = sdk
    plan = plans()[0]
    journal = writer.WriteAttemptJournal(tmp_path / "attempts.sqlite")
    moments = iter([NOW, NOW, NOW + 60])
    with pytest.raises(ReadFailure, match="R3_CLOUD_WRITE_OUTCOME_UNKNOWN"):
        invoke(transport, plan, signed(grant(plan, journal)), journal, clock=lambda: next(moments))
    assert calls == []
    with pytest.raises(ReadFailure, match="R3_WRITE_REPLAY_DISALLOWED"):
        invoke(transport, plan, signed(grant(plan, journal)), journal)


def test_credentials_are_explicit_and_import_has_no_execute_entry():
    with pytest.raises(ReadFailure, match="R3_WRITE_CREDENTIALS_REQUIRED"):
        writer.CloudWriteTransport.authenticated("", "")
    assert not hasattr(writer, "main") and not hasattr(writer, "apply")
