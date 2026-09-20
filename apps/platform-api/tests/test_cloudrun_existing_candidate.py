from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import deploy_cloudrun_api as release

FIXTURES = Path(__file__).parent / "fixtures/cloudrun_release"
STABLE_COMMIT = "a" * 40
CANDIDATE_COMMIT = release.EXPECTED_CANDIDATE_RUNTIME_COMMIT
CANDIDATE = "seiwajyuku-platform-api-257"
STABLE = "seiwajyuku-platform-api-253"
REAL_VERIFY_CONTROLLER = release.verify_controller_provenance


@pytest.fixture(autouse=True)
def isolated_controller_ci(monkeypatch):
    # Controller tests use a trusted fake; separate tests below exercise the
    # real provenance verifier without GitHub or cloud access.
    monkeypatch.setattr(release, "verify_controller_provenance", lambda *args: None)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def candidate_fixture(**overrides: object) -> dict:
    value = fixture("candidate_matching_vpc.json")
    value["EnvParams"] = json.dumps(
        {
            "G5_4_PRODUCTION_RULE_APPLY_ENABLED": "false",
            "LEARNING_CREDIT_SETTLEMENT_ENABLED": "false",
            "RUN_BOOTSTRAP_ON_STARTUP": "false",
        }
    )
    value.update(overrides)
    return value


class ExistingCandidateApi:
    def __init__(self, *, candidate: dict | None = None) -> None:
        self.service = fixture("service_base_empty.json")
        self.stable = fixture("stable_253.json")
        self.candidate = deepcopy(candidate or candidate_fixture())
        self.calls: list[str] = []
        self.create_calls = 0
        self.targeted_calls: list[tuple[str, str, str]] = []
        self.flow_calls: list[int] = []
        self.release_order: dict = {"TrafficType": "FLOW", "IsReleasing": False}
        self.active_release_order = False
        self.route_never_active = False
        self.route_delay_polls = 0
        self.route_installed = False
        self.pods: list[dict] = [{"Status": "Running", "PodId": "candidate-pod"}]
        self.log_rows: list[dict] | None = None
        self.restore_bad = False
        self.task_status = "stopped"

    def describe_service(self) -> dict:
        self.calls.append("describe_service")
        return deepcopy(self.service)

    def describe_version(self, version_name: str) -> dict:
        self.calls.append(f"describe_version:{version_name}")
        if version_name == STABLE:
            return deepcopy(self.stable)
        if version_name == CANDIDATE:
            return deepcopy(self.candidate)
        raise KeyError(version_name)

    def describe_deploy_records(self) -> list[dict]:
        self.calls.append("describe_deploy_records")
        return [
            {
                "DeployId": "253",
                "Status": "normal",
                "IsReleasing": False,
            },
            {
                "DeployId": "257",
                "Status": "normal",
                "IsReleasing": False,
            },
        ]

    def describe_manage_task(self, task_id: int) -> dict:
        self.calls.append(f"describe_task:{task_id}")
        return {"Id": 2190170, "Status": self.task_status, "VersionName": CANDIDATE}

    def describe_release_order(self) -> dict:
        self.calls.append("describe_release_order")
        if self.active_release_order:
            return {
                "TrafficType": "FLOW",
                "IsReleasing": True,
                "ReleaseStatus": "gray",
            }
        if self.route_installed and self.route_never_active:
            return {"TrafficType": "FLOW", "IsReleasing": True}
        if self.route_installed and self.route_delay_polls > 0:
            self.route_delay_polls -= 1
            return {"TrafficType": "FLOW", "IsReleasing": True}
        return deepcopy(self.release_order)

    def describe_candidate_pods(self, version_name: str) -> list[dict]:
        self.calls.append(f"describe_pods:{version_name}")
        return deepcopy(self.pods)

    def search_cls_logs(self, query: str, start_time: str, end_time: str) -> dict:
        self.calls.append("search_cls_logs")
        if self.log_rows is not None:
            return {"Results": deepcopy(self.log_rows)}
        return {
            "Results": [
                {"Content": {"__TAG__": {"container_name": CANDIDATE}}}
                for _ in range(23)
            ]
        }

    def create_candidate(self, spec: release.UpdateRequestSpec) -> int:
        self.create_calls += 1
        raise AssertionError("existing-candidate mode must never create a revision")

    def release_targeted(
        self, stable_revision: str, candidate_revision: str, token: str
    ) -> None:
        self.calls.append("release_targeted")
        self.targeted_calls.append((stable_revision, candidate_revision, token))
        self.route_installed = True
        self.release_order = {
            "TrafficType": "URL_PARAMS",
            "CurrentVersion": {
                "VersionName": stable_revision,
                "FlowRatio": 100,
                "IsDefaultPriority": True,
            },
            "ReleaseVersion": {
                "VersionName": candidate_revision,
                "FlowRatio": 0,
                "IsDefaultPriority": False,
                "UrlParam": {"Key": release.CANARY_QUERY_KEY, "Value": token},
            },
            "TrafficTypeValues": [{"Key": release.CANARY_QUERY_KEY, "Value": token}],
            "IsReleasing": True,
            "ReleaseStatus": "gray",
        }

    def release_flow(
        self, stable_revision: str, candidate_revision: str, candidate_percent: int
    ) -> None:
        self.calls.append(f"release_flow:{candidate_percent}")
        self.flow_calls.append(candidate_percent)
        if candidate_percent != 0:
            raise AssertionError("existing-candidate mode may only restore 0% FLOW")
        self.service["OnlineVersionInfos"] = [
            {"VersionName": stable_revision, "FlowRatio": "100"},
            {"VersionName": candidate_revision, "FlowRatio": "0"},
        ]
        self.release_order = {
            "TrafficType": "FLOW",
            "TrafficTypeValues": [],
            "IsReleasing": self.active_release_order,
            "ReleaseStatus": "gray" if self.active_release_order else "success",
        }
        self.route_installed = False
        if self.restore_bad:
            self.service["OnlineVersionInfos"] = [
                {"VersionName": candidate_revision, "FlowRatio": "100"}
            ]


class ExistingCandidateProbe:
    def __init__(self, api: ExistingCandidateApi) -> None:
        self.api = api
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.db_calls = 0
        self.fail_db_at: int | None = None
        self.candidate_commit = CANDIDATE_COMMIT
        self.liveness_status = "ok"

    def get_json(
        self,
        base_url: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
    ) -> release.HttpResult:
        routed = dict(query or {})
        self.calls.append((path, routed))
        if path == "/api/v1/system/build-info":
            return release.HttpResult(
                200,
                {"commit_sha": self.candidate_commit if routed else STABLE_COMMIT},
            )
        if path == "/health/live":
            return release.HttpResult(200, {"status": self.liveness_status})
        if path == "/api/v1/health":
            self.db_calls += 1
            if self.fail_db_at == self.db_calls:
                return release.HttpResult(500, {"status": "failed"})
            return release.HttpResult(200, {"status": "ok"})
        raise AssertionError(path)


def make_controller() -> tuple[
    release.CloudRunReleaseController, ExistingCandidateApi, ExistingCandidateProbe
]:
    api = ExistingCandidateApi()
    probe = ExistingCandidateProbe(api)
    controller = release.CloudRunReleaseController(
        api, probe, sleep=lambda _: None, poll_seconds=0, max_polls=2
    )
    return controller, api, probe


def build_plan(
    controller: release.CloudRunReleaseController,
) -> release.ExistingCandidatePlan:
    return controller.build_existing_candidate_plan(
        CANDIDATE,
        expected_candidate_runtime_commit=CANDIDATE_COMMIT,
        control_tool_commit=release.R2D_BASELINE_COMMIT,
        approval_ref="TEST-R2E-APPROVAL",
        expected_stable_revision=STABLE,
    )


def cls_rows(*, candidate: int, stable: int = 0, unknown: int = 0) -> list[dict]:
    return (
        [
            {"Content": {"__TAG__": {"container_name": CANDIDATE}}}
            for _ in range(candidate)
        ]
        + [{"Content": {"__TAG__": {"container_name": STABLE}}} for _ in range(stable)]
        + [{"Content": {"message": "no revision tag"}} for _ in range(unknown)]
    )


def test_existing_candidate_plan_reads_only_and_has_independent_provenance() -> None:
    controller, api, _ = make_controller()

    plan = build_plan(controller)

    assert plan.stable_revision == STABLE
    assert plan.candidate_revision == CANDIDATE
    assert plan.stable_vpc_conf == plan.candidate_vpc_conf
    assert plan.expected_candidate_runtime_commit == CANDIDATE_COMMIT
    assert plan.control_tool_commit == release.R2D_BASELINE_COMMIT
    assert plan.safe_dict()["new_revision_creation_path"] == "NOT_USED"
    assert api.create_calls == 0
    assert api.targeted_calls == []
    assert api.flow_calls == []


def test_missing_candidate_is_not_replaced() -> None:
    controller, api, _ = make_controller()

    with pytest.raises(release.ReleaseFailure) as error:
        controller.build_existing_candidate_plan("seiwajyuku-platform-api-999")

    assert error.value.code == "EXISTING_CANDIDATE_NOT_REUSABLE"
    assert api.create_calls == 0
    assert api.targeted_calls == []


def test_bad_candidate_status_is_not_reusable() -> None:
    controller, api, _ = make_controller()
    api.candidate["Status"] = "failed"

    with pytest.raises(release.ReleaseFailure) as error:
        build_plan(controller)

    assert error.value.code == "EXISTING_CANDIDATE_NOT_REUSABLE"
    assert api.create_calls == 0


def test_candidate_equal_to_stable_is_rejected() -> None:
    controller, api, _ = make_controller()
    api.candidate["Name"] = STABLE

    with pytest.raises(release.ReleaseFailure) as error:
        controller.build_existing_candidate_plan(STABLE)

    assert error.value.code == "EXISTING_CANDIDATE_NOT_REUSABLE"
    assert api.create_calls == 0


def test_candidate_ordinary_flow_must_be_zero() -> None:
    controller, api, _ = make_controller()
    api.service["OnlineVersionInfos"].append(
        {"VersionName": CANDIDATE, "FlowRatio": "5"}
    )
    api.service["OnlineVersionInfos"][0]["FlowRatio"] = "95"

    with pytest.raises(release.ReleaseFailure) as error:
        build_plan(controller)

    assert error.value.code == "EXISTING_CANDIDATE_TRAFFIC_UNEXPECTED"
    assert api.targeted_calls == []


def test_candidate_vpc_mismatch_is_rejected_before_route() -> None:
    controller, api, _ = make_controller()
    api.candidate["VpcConf"]["SubnetId"] = "subnet-other"

    with pytest.raises(release.ReleaseFailure) as error:
        build_plan(controller)

    assert error.value.code == "CANDIDATE_VPC_MISMATCH"
    assert api.targeted_calls == []
    assert api.flow_calls == []


@pytest.mark.parametrize("unsafe_key", release.EXISTING_CANDIDATE_GATE_KEYS)
def test_each_candidate_gate_must_be_explicit_false(unsafe_key: str) -> None:
    controller, api, _ = make_controller()
    env = json.loads(api.candidate["EnvParams"])
    env[unsafe_key] = "true"
    api.candidate["EnvParams"] = json.dumps(env)

    with pytest.raises(release.ReleaseFailure) as error:
        build_plan(controller)

    assert error.value.code == "EXISTING_CANDIDATE_GATE_UNSAFE"
    assert api.create_calls == 0


def test_missing_candidate_gate_is_unsafe() -> None:
    controller, api, _ = make_controller()
    env = json.loads(api.candidate["EnvParams"])
    del env["G5_4_PRODUCTION_RULE_APPLY_ENABLED"]
    api.candidate["EnvParams"] = json.dumps(env)

    with pytest.raises(release.ReleaseFailure) as error:
        build_plan(controller)

    assert error.value.code == "EXISTING_CANDIDATE_GATE_UNSAFE"


def test_active_release_order_blocks_existing_candidate_preflight() -> None:
    controller, api, _ = make_controller()
    api.active_release_order = True

    with pytest.raises(release.ReleaseFailure) as error:
        build_plan(controller)

    assert error.value.code == "ACTIVE_RELEASE_ORDER_EXISTS"
    assert api.create_calls == 0


def test_active_manage_task_blocks_existing_candidate_preflight() -> None:
    controller, api, _ = make_controller()
    api.task_status = "running"

    with pytest.raises(release.ReleaseFailure) as error:
        build_plan(controller)

    assert error.value.code == "STABLE_REVISION_NOT_UNIQUE"
    assert api.create_calls == 0


def test_dry_run_plan_has_zero_write_calls() -> None:
    controller, api, _ = make_controller()

    plan = build_plan(controller)
    safe = plan.safe_dict()

    assert safe["future_write_scope"]["revision_writes"] == 0
    assert safe["future_write_scope"]["database_writes"] == 0
    assert api.create_calls == 0
    assert api.targeted_calls == []
    assert api.flow_calls == []


def test_existing_verification_requires_approval_before_route_write() -> None:
    controller, api, _ = make_controller()
    plan = controller.build_existing_candidate_plan(CANDIDATE)

    with pytest.raises(release.ReleaseFailure) as error:
        controller.verify_existing_candidate(plan)

    assert error.value.code == "APPROVAL_REFERENCE_MISSING"
    assert api.targeted_calls == []


def test_success_uses_one_url_params_route_then_one_flow_restore() -> None:
    controller, api, probe = make_controller()
    plan = build_plan(controller)

    result = controller.verify_existing_candidate(plan)

    assert result["candidate_database_health"] == "20/20"
    assert result["targeted_probe_count"] == 23
    assert result["candidate_identity"] == "candidate=23/stable=0/unknown=0"
    assert result["traffic_restored"] is True
    assert api.targeted_calls and api.flow_calls == [0]
    assert api.create_calls == 0
    assert release.ReleaseState.CREATE_CANDIDATE not in controller.history
    assert controller.history == [
        release.ReleaseState.DISCOVER_STABLE,
        release.ReleaseState.READ_EXISTING_CANDIDATE,
        release.ReleaseState.VERIFY_CANDIDATE_CONFIG,
        release.ReleaseState.DISCOVER_STABLE,
        release.ReleaseState.READ_EXISTING_CANDIDATE,
        release.ReleaseState.VERIFY_CANDIDATE_CONFIG,
        release.ReleaseState.SET_TARGETED_ROUTE,
        release.ReleaseState.TARGETED_ROUTE_ACTIVE,
        release.ReleaseState.CANDIDATE_INSTANCE_READY,
        release.ReleaseState.TARGETED_HEALTH,
        release.ReleaseState.CANDIDATE_IDENTITY_VERIFIED,
        release.ReleaseState.TRAFFIC_RESTORED,
        release.ReleaseState.CONTROL_PLANE_CLOSE_PENDING,
    ]
    targeted_probes = [(path, query) for path, query in probe.calls if query]
    assert len(targeted_probes) == 23
    assert all(
        set(query) == {release.CANARY_QUERY_KEY, release.PROBE_QUERY_KEY}
        for _, query in targeted_probes
    )


def test_route_activation_requires_url_params_before_any_probe() -> None:
    controller, api, probe = make_controller()
    api.route_never_active = True
    plan = build_plan(controller)

    with pytest.raises(release.ReleaseFailure) as error:
        controller.verify_existing_candidate(plan)

    assert error.value.code == "CANDIDATE_ROUTE_NOT_ACTIVE"
    assert not any(query for _, query in probe.calls)
    assert api.flow_calls == [0]


def test_route_activation_is_polled_before_targeted_health() -> None:
    controller, api, probe = make_controller()
    api.route_delay_polls = 1
    plan = build_plan(controller)

    controller.verify_existing_candidate(plan)

    route_index = api.calls.index("release_targeted")
    first_probe = next(
        index
        for index, (path, query) in enumerate(probe.calls)
        if query and path.endswith("build-info")
    )
    assert route_index >= 0
    assert first_probe >= 0


def test_candidate_instance_timeout_does_not_infer_explicit_start_requirement() -> None:
    controller, api, probe = make_controller()
    api.pods = []
    plan = build_plan(controller)

    with pytest.raises(release.ReleaseFailure) as error:
        controller.verify_existing_candidate(plan)

    assert error.value.code == "CANDIDATE_INSTANCE_NOT_READY"
    assert not any(query for _, query in probe.calls)
    assert api.flow_calls == [0]
    assert not any("start" in call.lower() for call in api.calls)


def test_candidate_runtime_commit_mismatch_stops_before_database_health() -> None:
    controller, api, probe = make_controller()
    probe.candidate_commit = "c" * 40
    plan = build_plan(controller)

    with pytest.raises(release.ReleaseFailure) as error:
        controller.verify_existing_candidate(plan)

    assert error.value.code == "CANDIDATE_PROVENANCE_MISMATCH"
    assert probe.db_calls == 0
    assert api.flow_calls == [0]


def test_database_health_failure_restores_stable_route() -> None:
    controller, api, probe = make_controller()
    probe.fail_db_at = 4
    plan = build_plan(controller)

    with pytest.raises(release.ReleaseFailure) as error:
        controller.verify_existing_candidate(plan)

    assert error.value.code == "CANDIDATE_DATABASE_NOT_READY"
    assert probe.db_calls == 4
    assert api.flow_calls == [0]


def test_liveness_failure_restores_stable_route() -> None:
    controller, api, probe = make_controller()
    probe.liveness_status = "failed"
    plan = build_plan(controller)

    with pytest.raises(release.ReleaseFailure) as error:
        controller.verify_existing_candidate(plan)

    assert error.value.code == "CANDIDATE_LIVENESS_NOT_READY"
    assert api.flow_calls == [0]


def test_cls_stable_identity_fails_closed() -> None:
    controller, api, _ = make_controller()
    api.log_rows = cls_rows(candidate=0, stable=23)
    plan = build_plan(controller)

    with pytest.raises(release.ReleaseFailure) as error:
        controller.verify_existing_candidate(plan)

    assert error.value.code == "CANDIDATE_IDENTITY_MISMATCH"
    assert api.flow_calls == [0]


def test_cls_mixed_identity_fails_closed() -> None:
    controller, api, _ = make_controller()
    api.log_rows = cls_rows(candidate=20, stable=3)
    plan = build_plan(controller)

    with pytest.raises(release.ReleaseFailure) as error:
        controller.verify_existing_candidate(plan)

    assert error.value.code == "CANDIDATE_IDENTITY_MISMATCH"


def test_cls_incomplete_or_unknown_identity_fails_closed() -> None:
    controller, api, _ = make_controller()
    api.log_rows = cls_rows(candidate=22, unknown=1)
    plan = build_plan(controller)

    with pytest.raises(release.ReleaseFailure) as error:
        controller.verify_existing_candidate(plan)

    assert error.value.code == "CANDIDATE_IDENTITY_NOT_PROVEN"


def test_restore_requires_stable_100_candidate_zero_confirmation() -> None:
    controller, api, _ = make_controller()
    api.restore_bad = True
    plan = build_plan(controller)

    with pytest.raises(release.ReleaseFailure) as error:
        controller.verify_existing_candidate(plan)

    assert error.value.code == "TRAFFIC_RESTORE_NOT_CONFIRMED"
    assert api.flow_calls == [0]
    assert error.value.evidence["traffic_restored"] is False


def test_release_close_is_reported_separately_when_order_remains_active() -> None:
    controller, api, _ = make_controller()
    api.active_release_order = True
    # The active-order preflight must be clear; keep it terminal until after
    # the targeted route has been restored, then expose the stale gray order.
    api.active_release_order = False
    original_restore = api.release_flow

    def restore_then_leave_order_active(
        stable_revision: str, candidate_revision: str, candidate_percent: int
    ) -> None:
        original_restore(stable_revision, candidate_revision, candidate_percent)
        api.active_release_order = True
        api.release_order["IsReleasing"] = True
        api.release_order["ReleaseStatus"] = "gray"

    api.release_flow = restore_then_leave_order_active  # type: ignore[method-assign]
    plan = build_plan(controller)

    result = controller.verify_existing_candidate(plan)

    assert result["traffic_restored"] is True
    assert result["release_order_closed"] is False
    assert result["manual_close_required"] is True


def test_provenance_commit_arguments_are_validated_separately() -> None:
    controller, api, _ = make_controller()

    with pytest.raises(release.ReleaseFailure) as error:
        controller.build_existing_candidate_plan(
            CANDIDATE, expected_candidate_runtime_commit="bad"
        )
    assert error.value.code == "CANDIDATE_RUNTIME_COMMIT_INVALID"
    assert api.create_calls == 0

    controller, api, _ = make_controller()
    with pytest.raises(release.ReleaseFailure) as error:
        controller.build_existing_candidate_plan(CANDIDATE, control_tool_commit="bad")
    assert error.value.code == "CONTROL_TOOL_COMMIT_INVALID"
    assert api.create_calls == 0


def test_cli_parser_exposes_independent_existing_candidate_mode() -> None:
    args = release._parser().parse_args(
        ["--verify-existing-candidate", CANDIDATE, "--dry-run"]
    )

    assert args.verify_existing_candidate == CANDIDATE
    assert args.dry_run is True
    assert args.source is False
    assert args.image_url is None


def test_cli_existing_mode_rejects_artifact_creation_flags() -> None:
    result = release.main(
        ["--verify-existing-candidate", CANDIDATE, "--dry-run", "--source"]
    )

    assert result == 2


def test_cli_existing_execute_requires_bounded_approval_reference() -> None:
    result = release.main(["--verify-existing-candidate", CANDIDATE, "--execute"])

    assert result == 2


def test_zero_pods_can_become_ready_without_instance_writes() -> None:
    controller, api, probe = make_controller()
    sequence = iter([[], [{"Status": "Ready"}]])
    api.describe_candidate_pods = lambda _: next(sequence)
    result = controller.verify_existing_candidate(build_plan(controller))
    assert result["new_revision_db_connectivity"] == "PASS"
    assert probe.db_calls == 20
    assert api.create_calls == 0


def test_explicit_start_requirement_stops_and_restores_without_start_call() -> None:
    controller, api, _ = make_controller()

    def requiring_start(_):
        raise release.ReleaseFailure(
            "EXPLICIT_CANDIDATE_START_REQUIRED",
            "explicit start required by platform evidence",
        )

    api.describe_candidate_pods = requiring_start
    with pytest.raises(release.ReleaseFailure) as error:
        controller.verify_existing_candidate(build_plan(controller))
    assert error.value.code == "EXPLICIT_CANDIDATE_START_REQUIRED"
    assert error.value.evidence["traffic_restored"] is True
    assert api.flow_calls == [0]


@pytest.mark.parametrize("field", ["VpcId", "VpcCIDR", "SubnetId", "SubnetCIDR"])
def test_each_vpc_field_and_empty_baseline_is_guarded(field) -> None:
    controller, api, _ = make_controller()
    api.candidate["VpcConf"][field] = "different"
    with pytest.raises(release.ReleaseFailure, match="VpcConf"):
        build_plan(controller)
    api.stable["VpcConf"][field] = ""
    api.candidate["VpcConf"][field] = ""
    with pytest.raises(release.ReleaseFailure) as error:
        build_plan(controller)
    assert error.value.code == "VPC_BASELINE_MISSING"
    assert api.targeted_calls == []


def test_execute_rechecks_candidate_environment_before_writing() -> None:
    controller, api, _ = make_controller()
    plan = build_plan(controller)
    api.candidate["EnvParams"] = "{}"
    with pytest.raises(release.ReleaseFailure) as error:
        controller.verify_existing_candidate(plan)
    assert error.value.code == "EXISTING_CANDIDATE_GATE_UNSAFE"
    assert api.targeted_calls == []


def test_approved_stable_is_checked_without_hardcoding_253() -> None:
    controller, api, _ = make_controller()
    with pytest.raises(release.ReleaseFailure) as error:
        controller.build_existing_candidate_plan(
            CANDIDATE, expected_stable_revision="seiwajyuku-platform-api-999"
        )
    assert error.value.code == "STABLE_REVISION_CHANGED"
    assert api.targeted_calls == []


def test_restore_with_residual_target_token_is_not_a_pass() -> None:
    controller, api, _ = make_controller()
    original = api.release_flow

    def residual(stable, candidate, percent):
        original(stable, candidate, percent)
        api.release_order["TrafficTypeValues"] = [
            {"Key": "sj_canary", "Value": "residual"}
        ]

    api.release_flow = residual
    with pytest.raises(release.ReleaseFailure) as error:
        controller.verify_existing_candidate(build_plan(controller))
    assert error.value.code == "TRAFFIC_RESTORE_NOT_CONFIRMED"
    assert error.value.evidence["traffic_restored"] is False
    assert api.flow_calls == [0]


@pytest.mark.parametrize("kind", ["unknown", "running"])
def test_manage_task_must_be_terminal_for_close_pass(kind) -> None:
    controller, api, _ = make_controller()
    original = api.release_flow

    def task_status_after_restore(stable, candidate, percent):
        original(stable, candidate, percent)
        api.task_status = kind

    api.release_flow = task_status_after_restore
    result = controller.verify_existing_candidate(build_plan(controller))
    assert result["traffic_restored"] is True
    assert result["release_order_closed"] is False
    assert result["manual_close_required"] is True


@pytest.mark.parametrize(
    "operation",
    [
        "UpdateCloudRunServer",
        "SubmitServerConfigChangeDiff",
        "StartVersionInstance",
        "StopVersionInstance",
        "DeleteCloudRunVersions",
        "OperateServerManage",
    ],
)
def test_sdk_capability_boundary_rejects_prohibited_apis(operation) -> None:
    api = object.__new__(release.ExistingCandidateSdkApi)
    with pytest.raises(release.ReleaseFailure) as error:
        api._tcbr_request(operation, object())
    assert error.value.code == "EXISTING_CANDIDATE_WRITE_DISALLOWED"


@pytest.mark.parametrize("percent", [1, 5, 100])
def test_sdk_capability_boundary_disallows_canary_and_full(percent) -> None:
    api = object.__new__(release.ExistingCandidateSdkApi)
    with pytest.raises(release.ReleaseFailure):
        api.release_flow(STABLE, CANDIDATE, percent)


def test_cli_existing_execution_cannot_enter_create_path(monkeypatch, capsys) -> None:
    _, api, probe = make_controller()
    monkeypatch.setattr(release, "ExistingCandidateSdkApi", lambda: api)
    monkeypatch.setattr(release, "UrlLibProbe", lambda: probe)

    def forbidden(*args, **kwargs):
        pytest.fail("ordinary create path was entered")

    monkeypatch.setattr(release.CloudRunReleaseController, "execute", forbidden)
    result = release.main(
        [
            "--verify-existing-candidate",
            CANDIDATE,
            "--execute",
            "--approval-ref",
            "TEST",
            "--control-tool-commit",
            release.R2D_BASELINE_COMMIT,
            "--expected-stable-revision",
            STABLE,
        ]
    )
    assert result == 0
    assert api.create_calls == 0
    assert api.flow_calls == [0]
    assert len(api.targeted_calls) == 1
    assert json.loads(capsys.readouterr().out)["new_revision_db_connectivity"] == "PASS"


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "missing",
        "dirty",
        "tree",
        "sha",
        "branch",
        "event",
        "workflow",
        "job_failed",
        "job_missing",
        "job_skipped",
    ],
)
def test_real_controller_provenance_gate(monkeypatch, fault) -> None:
    from types import SimpleNamespace

    import build_provenance

    commit = "d" * 40
    identity = {"release_commit": commit, "source_tree_sha": "e" * 40}
    manifest = {**identity, "github_ci_run": "123"}

    def checkout(root, **kwargs):
        assert kwargs == {"expected_commit": commit, "require_origin_main": True}
        assert root == REPO_ROOT
        if fault == "dirty":
            raise RuntimeError("dirty")
        return identity

    monkeypatch.setattr(build_provenance, "verify_checkout", checkout)
    monkeypatch.setattr(build_provenance, "validate_manifest", lambda _: manifest)
    if fault == "tree":
        manifest["source_tree_sha"] = "f" * 40
    run = {
        "head_sha": commit,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "path": ".github/workflows/ci.yml",
    }
    for case, key, value in [
        ("sha", "head_sha", "f" * 40),
        ("branch", "head_branch", "feature"),
        ("event", "event", "pull_request"),
        ("workflow", "path", "other.yml"),
    ]:
        if fault == case:
            run[key] = value
    jobs = [
        {"name": name, "status": "completed", "conclusion": "success"}
        for name in release.CONTROLLER_CI_JOBS
    ]
    if fault in {"job_failed", "job_skipped"}:
        jobs[0]["conclusion"] = "failure" if fault == "job_failed" else "skipped"
    if fault == "job_missing":
        jobs.pop()

    def gh(args, **kwargs):
        assert args[:2] == ["gh", "api"]
        assert kwargs["encoding"] == "utf-8"
        payload = (
            {"total_count": len(jobs), "jobs": jobs} if "/jobs?" in args[2] else run
        )
        return SimpleNamespace(stdout=json.dumps(payload))

    monkeypatch.setattr(release.subprocess, "run", gh)
    path = None if fault == "missing" else Path("controller-manifest.json")
    if fault is None:
        REAL_VERIFY_CONTROLLER(commit, path)
    else:
        with pytest.raises(release.ReleaseFailure) as error:
            REAL_VERIFY_CONTROLLER(commit, path)
        assert error.value.code.startswith("CONTROLLER_PROVENANCE_")
