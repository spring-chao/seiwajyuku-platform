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
DESIRED_COMMIT = "b" * 40


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeApi:
    def __init__(self, *, candidate: dict | None = None) -> None:
        self.service = fixture("service_base_empty.json")
        self.stable = fixture("stable_253.json")
        self.candidate = candidate or fixture("candidate_matching_vpc.json")
        self.records = [
            {
                "DeployId": "253",
                "DeployTime": "2026-09-19 10:00:00",
                "Status": "running",
                "IsReleasing": False,
            }
        ]
        self.events: list[str] = []
        self.update_specs: list[release.UpdateRequestSpec] = []
        self.targeted_calls: list[tuple[str, str, str]] = []
        self.flow_calls: list[int] = []
        self.full_active = False
        self.candidate_name = str(self.candidate["Name"])

    def describe_service(self) -> dict:
        self.events.append("describe_service")
        return deepcopy(self.service)

    def describe_version(self, version_name: str) -> dict:
        self.events.append(f"describe_version:{version_name}")
        if version_name == self.stable["Name"]:
            return deepcopy(self.stable)
        if version_name == self.candidate_name:
            return deepcopy(self.candidate)
        raise AssertionError(f"unexpected version: {version_name}")

    def describe_deploy_records(self) -> list[dict]:
        self.events.append("describe_deploy_records")
        return deepcopy(self.records)

    def describe_manage_task(self, task_id: int) -> dict:
        self.events.append(f"describe_task:{task_id}")
        return {
            "Id": task_id,
            "Status": "finished",
            "VersionName": self.candidate_name,
        }

    def create_candidate(self, spec: release.UpdateRequestSpec) -> int:
        self.events.append("create_candidate")
        self.update_specs.append(spec)
        return 9001

    def release_targeted(
        self, stable_revision: str, candidate_revision: str, token: str
    ) -> None:
        self.events.append("release_targeted")
        self.targeted_calls.append((stable_revision, candidate_revision, token))

    def release_flow(
        self, stable_revision: str, candidate_revision: str, candidate_percent: int
    ) -> None:
        self.events.append(f"release_flow:{candidate_percent}")
        self.flow_calls.append(candidate_percent)
        self.full_active = candidate_percent == 100


class FakeProbe:
    def __init__(self, api: FakeApi) -> None:
        self.api = api
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.db_calls = 0
        self.fail_db_at: int | None = None
        self.candidate_commit = DESIRED_COMMIT

    def get_json(
        self,
        base_url: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
    ) -> release.HttpResult:
        routed = dict(query or {})
        self.calls.append((path, routed))
        self.api.events.append(f"probe:{path}")
        candidate = bool(routed) or self.api.full_active
        if path == "/api/v1/system/build-info":
            return release.HttpResult(
                200,
                {"commit_sha": self.candidate_commit if candidate else STABLE_COMMIT},
            )
        if path == "/health/live":
            return release.HttpResult(
                200, {"status": "ok", "service": release.SERVICE_NAME}
            )
        if path == "/api/v1/health":
            self.db_calls += 1
            if self.fail_db_at == self.db_calls:
                return release.HttpResult(500, {"status": "failed"})
            return release.HttpResult(
                200, {"status": "ok", "service": release.SERVICE_NAME}
            )
        raise AssertionError(f"unexpected probe path: {path}")


class FakeSourceProvider:
    def __init__(self) -> None:
        self.calls = 0

    def prepare(self, plan: release.ReleasePlan) -> release.DeployArtifact:
        self.calls += 1
        return release.DeployArtifact(
            deploy_type="package",
            package_name="fixture-package",
            package_version="fixture-version",
        )


def make_controller(
    *, candidate: dict | None = None
) -> tuple[release.CloudRunReleaseController, FakeApi, FakeProbe, FakeSourceProvider]:
    api = FakeApi(candidate=candidate)
    probe = FakeProbe(api)
    source = FakeSourceProvider()
    controller = release.CloudRunReleaseController(
        api,
        probe,
        source_provider=source,
        sleep=lambda _: None,
        poll_seconds=0,
        max_polls=3,
    )
    return controller, api, probe, source


def source_input(**overrides) -> release.ReleaseInput:
    values = {
        "desired_runtime_commit": DESIRED_COMMIT,
        "artifact_mode": "source",
        "requested_env_changes": {},
        "approval_ref": "TEST-APPROVAL",
        "build_id": "test-build",
        "ci_provenance_verified": True,
    }
    values.update(overrides)
    return release.ReleaseInput(**values)


def test_stable_discovery_uses_unique_100_percent_version_revision() -> None:
    controller, api, _, _ = make_controller()

    plan = controller.build_plan(source_input())

    assert plan.stable_revision == "seiwajyuku-platform-api-253"
    assert plan.stable_runtime_commit == STABLE_COMMIT
    assert plan.stable_vpc_conf.vpc_id == "vpc-5l5vzkr2"
    assert plan.stable_vpc_conf.subnet_id == "subnet-1xw5yqjh"
    # The empty service-level baseline is deliberately ignored.
    assert api.service["ServerConfig"]["VpcConf"]["VpcId"] == ""


@pytest.mark.parametrize(
    "versions",
    [
        [
            {"VersionName": "stable", "FlowRatio": "95"},
            {"VersionName": "candidate", "FlowRatio": "5"},
        ],
        [{"VersionName": "stable", "FlowRatio": "99"}],
        [],
    ],
)
def test_split_or_missing_stable_fails_closed(versions: list[dict]) -> None:
    controller, api, _, _ = make_controller()
    api.service["OnlineVersionInfos"] = versions

    with pytest.raises(release.ReleaseFailure) as error:
        controller.build_plan(source_input())

    assert error.value.code == "STABLE_REVISION_NOT_UNIQUE"


def test_active_release_fails_stable_discovery() -> None:
    controller, api, _, _ = make_controller()
    api.records[0]["IsReleasing"] = True

    with pytest.raises(release.ReleaseFailure) as error:
        controller.build_plan(source_input())

    assert error.value.code == "STABLE_REVISION_NOT_UNIQUE"


def test_active_manage_task_fails_stable_discovery() -> None:
    controller, api, _, _ = make_controller()
    api.describe_manage_task = lambda _: {  # type: ignore[method-assign]
        "Id": 42,
        "Status": "running",
        "VersionName": "",
    }

    with pytest.raises(release.ReleaseFailure) as error:
        controller.build_plan(source_input())

    assert error.value.code == "STABLE_REVISION_NOT_UNIQUE"
    assert controller.history[-1] == release.ReleaseState.BLOCKED


def test_stable_revision_missing_vpc_fails_closed() -> None:
    controller, api, _, _ = make_controller()
    api.stable["VpcConf"]["VpcId"] = ""

    with pytest.raises(release.ReleaseFailure) as error:
        controller.build_plan(source_input())

    assert error.value.code == "VPC_BASELINE_MISSING"


def test_optional_database_vpc_assertion_is_read_only_and_fail_closed() -> None:
    controller, _, _, _ = make_controller()

    with pytest.raises(release.ReleaseFailure) as error:
        controller.build_plan(source_input(expected_database_vpc_id="vpc-other"))

    assert error.value.code == "VPC_DATABASE_MISMATCH"


def test_code_release_request_always_carries_stable_vpc() -> None:
    controller, api, _, source = make_controller()
    plan = controller.build_plan(source_input())

    controller.execute(plan)

    assert source.calls == 1
    assert api.update_specs[0].vpc_conf == plan.stable_vpc_conf
    assert api.update_specs[0].env_params_json is None


def test_live_release_requires_successful_main_ci_provenance() -> None:
    controller, api, _, source = make_controller()
    plan = controller.build_plan(source_input(ci_provenance_verified=False))

    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan)

    assert error.value.code == "CI_PROVENANCE_MISSING"
    assert source.calls == 0
    assert api.update_specs == []


def test_stable_revision_drift_blocks_before_candidate_creation() -> None:
    controller, api, _, _ = make_controller()
    plan = controller.build_plan(source_input())
    api.service["OnlineVersionInfos"] = [
        {
            "VersionName": "seiwajyuku-platform-api-999",
            "ImageUrl": "registry.example.test/new",
            "FlowRatio": "100",
        }
    ]

    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan)

    assert error.value.code == "STABLE_REVISION_CHANGED"
    assert api.update_specs == []
    assert api.targeted_calls == []


def test_normal_env_update_merges_stable_env_and_carries_vpc() -> None:
    controller, api, _, _ = make_controller()
    plan = controller.build_plan(
        release.ReleaseInput(
            desired_runtime_commit=STABLE_COMMIT,
            artifact_mode="stable-image",
            requested_env_changes={"G5_4_PRODUCTION_RULE_APPLY_ENABLED": "true"},
            approval_ref="TEST-CONFIG",
            ci_provenance_verified=True,
        )
    )

    assert plan.desired_vpc_conf == plan.stable_vpc_conf
    merged = json.loads(plan.env_params_json or "{}")
    assert merged["DATABASE_URL"] == "fixture-sensitive-value"
    assert merged["G5_4_PRODUCTION_RULE_APPLY_ENABLED"] == "true"
    # Use the stable image commit for this configuration-only candidate.
    api.candidate["Name"] = api.candidate_name
    controller.probe.candidate_commit = STABLE_COMMIT  # type: ignore[attr-defined]
    controller.execute(plan)
    assert api.update_specs[0].vpc_conf == plan.stable_vpc_conf


def test_typed_update_request_contains_explicit_vpc_diff_item() -> None:
    vpc = release.VpcConfiguration(
        "vpc-fixture", "10.0.0.0/16", "subnet-fixture", "10.0.1.0/24"
    )
    request = release.build_update_request(
        release.UpdateRequestSpec(
            artifact=release.DeployArtifact(
                deploy_type="package",
                package_name="package",
                package_version="version",
            ),
            vpc_conf=vpc,
            env_params_json=None,
            deploy_remark="test",
        )
    )

    payload = request._serialize()
    assert request.__class__.__name__ == "UpdateCloudRunServerRequest"
    assert payload["EnvId"] == release.ENV_ID
    assert payload["ServerName"] == release.SERVICE_NAME
    assert payload["DeployInfo"]["ReleaseType"] == "GRAY"
    assert payload["Items"] == [{"Key": "VpcConf", "VpcConf": vpc.as_api_dict()}]


def test_candidate_exact_vpc_match_allows_targeted_health() -> None:
    controller, api, probe, _ = make_controller()
    plan = controller.build_plan(source_input())

    result = controller.execute(plan)

    assert result["state"] == release.ReleaseState.READY_FOR_RELEASE.value
    assert result["targeted_database_health"] == "20/20"
    assert len(api.targeted_calls) == 1
    assert api.flow_calls == [0]
    assert probe.db_calls == 20
    routed_calls = [query for _, query in probe.calls if query]
    assert routed_calls
    assert all(set(query) == {release.CANARY_QUERY_KEY} for query in routed_calls)


def test_zero_task_id_cannot_reuse_an_old_finished_revision() -> None:
    controller, api, _, _ = make_controller()
    calls = 0

    def describe_task(_: int) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            api.records.append(
                {
                    "DeployId": "257",
                    "DeployTime": "2026-09-19 21:00:00",
                    "Status": "running",
                    "IsReleasing": False,
                }
            )
            return {
                "Id": 0,
                "Status": "finished",
                "VersionName": "seiwajyuku-platform-api-253",
            }
        return {
            "Id": 0,
            "Status": "finished",
            "VersionName": api.candidate_name,
        }

    plan = controller.build_plan(source_input())
    api.describe_manage_task = describe_task  # type: ignore[method-assign]

    result = controller.execute(plan)

    assert result["candidate_revision"] == "seiwajyuku-platform-api-257"
    assert api.stable["Name"] != result["candidate_revision"]


@pytest.mark.parametrize(
    "candidate_factory",
    [
        lambda: fixture("candidate_missing_vpc.json"),
        lambda: {
            **fixture("candidate_matching_vpc.json"),
            "VpcConf": {
                **fixture("candidate_matching_vpc.json")["VpcConf"],
                "SubnetId": "",
            },
        },
        lambda: {
            **fixture("candidate_matching_vpc.json"),
            "VpcConf": {
                **fixture("candidate_matching_vpc.json")["VpcConf"],
                "SubnetId": "subnet-other",
            },
        },
        lambda: {
            **fixture("candidate_matching_vpc.json"),
            "VpcConf": {
                **fixture("candidate_matching_vpc.json")["VpcConf"],
                "SubnetCIDR": "172.17.32.0/20",
            },
        },
    ],
)
def test_candidate_missing_partial_or_changed_vpc_never_gets_traffic(
    candidate_factory,
) -> None:
    controller, api, probe, _ = make_controller(candidate=candidate_factory())
    plan = controller.build_plan(source_input())

    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan)

    assert error.value.code == "CANDIDATE_VPC_MISMATCH"
    assert api.targeted_calls == []
    assert api.flow_calls == []
    assert not any(path == "/api/v1/health" for path, _ in probe.calls)
    assert controller.history[-1] == release.ReleaseState.FAILED


def test_network_assertion_happens_before_any_candidate_health() -> None:
    controller, api, _, _ = make_controller()
    plan = controller.build_plan(source_input())

    controller.execute(plan)

    candidate_describes = [
        index
        for index, event in enumerate(api.events)
        if event == f"describe_version:{api.candidate_name}"
    ]
    route_index = api.events.index("release_targeted")
    first_candidate_probe = next(
        index
        for index, event in enumerate(api.events)
        if event == "probe:/api/v1/system/build-info" and index > route_index
    )
    assert candidate_describes[-1] < route_index < first_candidate_probe


def test_database_health_failure_restores_stable_without_normal_candidate_flow() -> (
    None
):
    controller, api, probe, _ = make_controller()
    probe.fail_db_at = 7
    plan = controller.build_plan(source_input())

    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan)

    assert error.value.code == "CANDIDATE_DATABASE_NOT_READY"
    assert probe.db_calls == 7
    assert api.flow_calls == [0]
    assert 5 not in api.flow_calls and 100 not in api.flow_calls


def test_candidate_build_provenance_mismatch_stops_before_health() -> None:
    controller, api, probe, _ = make_controller()
    probe.candidate_commit = "c" * 40
    plan = controller.build_plan(source_input())

    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan)

    assert error.value.code == "CANDIDATE_PROVENANCE_MISMATCH"
    assert len(api.targeted_calls) == 1
    assert api.flow_calls == [0]
    assert probe.db_calls == 0


def test_sensitive_environment_values_are_never_in_safe_plan() -> None:
    controller, _, _, _ = make_controller()
    plan = controller.build_plan(
        source_input(
            requested_env_changes={
                "DATABASE_URL": "mysql://user:password@db/production",
                "JWT_SECRET": "do-not-print",
                "PUBLIC_LABEL": "also-not-printed",
                "G5_4_PRODUCTION_RULE_APPLY_ENABLED": "false",
            }
        )
    )

    rendered = json.dumps(plan.safe_dict(), ensure_ascii=False)
    assert "mysql://" not in rendered
    assert "do-not-print" not in rendered
    assert "also-not-printed" not in rendered
    assert '"value": "false"' in rendered


def test_dry_run_build_plan_has_zero_write_calls() -> None:
    controller, api, _, source = make_controller()

    plan = controller.build_plan(source_input())
    output = plan.safe_dict()

    assert output["stable_revision"] == "seiwajyuku-platform-api-253"
    assert output["candidate_request_vpc_conf"] == plan.stable_vpc_conf.as_api_dict()
    assert api.update_specs == []
    assert api.targeted_calls == []
    assert api.flow_calls == []
    assert source.calls == 0


def test_targeted_route_is_typed_url_parameter_with_stable_default() -> None:
    request = release.build_targeted_release_request(
        "seiwajyuku-platform-api-253",
        "seiwajyuku-platform-api-257",
        "one-time-token",
    )

    payload = request._serialize()
    assert payload["TrafficType"] == "URL_PARAMS"
    assert payload["VersionFlowItems"][0]["IsDefaultPriority"] is True
    assert payload["VersionFlowItems"][1]["IsDefaultPriority"] is False
    assert payload["VersionFlowItems"][1]["UrlParam"] == {
        "Key": release.CANARY_QUERY_KEY,
        "Value": "one-time-token",
    }


def test_full_promotion_cannot_skip_ready_and_gray_states() -> None:
    controller, api, _, _ = make_controller()
    plan = controller.build_plan(source_input())

    result = controller.execute(plan, promotion="full", gray_percent=7)

    assert result["state"] == release.ReleaseState.VERIFIED.value
    assert api.flow_calls == [7, 100]
    expected = [
        release.ReleaseState.DISCOVER_STABLE,
        release.ReleaseState.READ_STABLE_VERSION,
        release.ReleaseState.BUILD_PLAN,
        release.ReleaseState.CREATE_CANDIDATE,
        release.ReleaseState.VERIFY_CANDIDATE_CONFIG,
        release.ReleaseState.TARGETED_HEALTH,
        release.ReleaseState.READY_FOR_RELEASE,
        release.ReleaseState.GRAY,
        release.ReleaseState.FULL,
        release.ReleaseState.VERIFIED,
    ]
    assert controller.history == expected
