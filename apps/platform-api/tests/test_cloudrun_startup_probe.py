import hashlib
import json

import pytest
from test_cloudrun_vpc_repair import repair_fixture
from test_cloudrun_release_network_guard import source_input
import deploy_cloudrun_api as release


def startup_fixture():
    controller, api, probe, source, repair, *_ = repair_fixture()
    api.stable["Port"] = 8000
    api.candidate["Port"] = 8000
    plan = controller.build_plan(source_input(candidate_verification="startup-loopback"), network_repair=repair)
    proof = {"id": "sj_result_" + hashlib.sha256(plan.startup_probe_id.encode()).hexdigest(),
        "status": "passed", "commit_sha": plan.desired_runtime_commit, "loopback": "127.0.0.1:8000",
        "http_requests": 23, "database_health_passed": 20}
    original = api.search_cls_logs
    def logs(query, start, end):
        if proof["id"] in query:
            return {"Results": [{"Content": {"container_name": api.candidate_name,
                "message": "CANDIDATE_PROBE_RESULT " + json.dumps(proof)}}]}
        return original(query, start, end)
    api.search_cls_logs = logs
    return controller, api, probe, source, plan, proof


def test_startup_proof_and_23_identity_logs_precede_any_normal_flow():
    controller, api, _, _, plan, _ = startup_fixture()
    assert plan.startup_probe_id not in str(plan.safe_dict())
    result = controller.execute(plan, promotion="full")
    assert result["state"] == "VERIFIED" and result["candidate_verification"] == "startup-loopback"
    assert api.targeted_calls == [] and api.flow_calls == [5, 100]
    assert api.events.index("search_cls_logs") < api.events.index("release_flow:5")


@pytest.mark.parametrize("key,value", [("status", "failed"), ("commit_sha", "0" * 40),
    ("http_requests", 22), ("database_health_passed", 19), ("loopback", "other:8000")])
def test_invalid_startup_result_prevents_candidate_normal_flow(key, value):
    controller, api, _, _, plan, proof = startup_fixture()
    proof[key] = value
    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan, promotion="full")
    assert error.value.code == "CANDIDATE_SELF_PROBE_FAILED"
    assert api.targeted_calls == [] and api.flow_calls == []


def test_startup_proof_still_requires_23_candidate_logs_without_stable():
    controller, api, _, _, plan, _ = startup_fixture()
    api.log_rows = [{"Content": {"container_name": api.stable["Name"]}}]
    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan, promotion="full")
    assert error.value.code == "CANDIDATE_IDENTITY_MISMATCH"
    assert api.flow_calls == []


def test_missing_startup_result_never_allows_normal_flow():
    controller, api, _, _, plan, _ = startup_fixture()
    api.search_cls_logs = lambda *_: {"Results": []}
    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan, promotion="full")
    assert error.value.code == "CANDIDATE_SELF_PROBE_NOT_PROVEN"
    assert api.flow_calls == []


def test_loopback_mode_does_not_bypass_ordinary_release_gate():
    controller, api, _, _, _, *_ = repair_fixture()
    with pytest.raises(release.ReleaseFailure) as error:
        controller.build_plan(source_input(candidate_verification="startup-loopback"))
    assert error.value.code == "CANDIDATE_VERIFICATION_INVALID"
    assert api.update_specs == []


def test_candidate_port_mismatch_prevents_normal_flow():
    controller, api, _, _, plan, _ = startup_fixture()
    original = api.describe_version
    def version(name):
        value = original(name)
        if name == api.candidate_name:
            value["Port"] = 9000
        return value
    api.describe_version = version
    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan, promotion="full")
    assert error.value.code == "CANDIDATE_SELF_PROBE_FAILED"
    assert api.flow_calls == []


@pytest.mark.parametrize("encoding", ["json", "double-json", "raw"])
def test_startup_proof_reads_cls_escaped_content_without_changing_identity_gate(encoding):
    controller, api, _, _, plan, proof = startup_fixture()
    original = api.search_cls_logs
    def logs(query, start, end):
        if proof["id"] in query:
            content = "INFO: CANDIDATE_PROBE_RESULT " + json.dumps(proof)
            if encoding == "json":
                content = json.dumps({"log": content})
            elif encoding == "double-json":
                content = json.dumps(json.dumps({"log": content}))
            return {"Results": [{"Source": api.candidate_name, "Content": content}]}
        return original(query, start, end)
    api.search_cls_logs = logs
    assert controller.execute(plan, promotion="full")["state"] == "VERIFIED"
    assert api.flow_calls == [5, 100]
