from copy import deepcopy

import pytest

from test_cloudrun_release_network_guard import make_controller, source_input
import deploy_cloudrun_api as release


def repair_fixture():
    controller, api, probe, source = make_controller()
    reference = deepcopy(api.stable)
    reference["Name"] = release.SERVICE_NAME + "-252"
    vpc = reference["VpcConf"]
    api.stable["VpcConf"] = {key: "" for key in vpc}
    api.stable["EnvParams"] = '{"DATABASE_URL":"mysql://user:private@db.example.test/db"}'
    original = api.describe_version
    api.describe_version = lambda name: deepcopy(reference) if name == reference["Name"] else original(name)
    database = {"Data": {"DbInfo": {"Status": "running"}, "NetInfo": {
        "VpcId": vpc["VpcId"], "SubnetId": vpc["SubnetId"], "PubNetAddress": "db.example.test:3306",
    }}}
    network = {
        "VpcSet": [{"VpcId": vpc["VpcId"], "CidrBlock": vpc["VpcCIDR"]}],
        "SubnetSet": [{"VpcId": vpc["VpcId"], "SubnetId": vpc["SubnetId"],
                       "CidrBlock": vpc["SubnetCIDR"], "Zone": release.REGION + "-2"}],
    }
    api.describe_database_network = lambda: deepcopy(database)
    api.describe_vpc_subnet = lambda *_: deepcopy(network)
    create = api.create_candidate
    def create_with_environment(spec):
        api.candidate["EnvParams"] = spec.env_params_json or api.stable["EnvParams"]
        return create(spec)
    api.create_candidate = create_with_environment
    repair = release.MissingVpcRepairInput(api.stable["Name"], reference["Name"])
    return controller, api, probe, source, repair, reference, database, network


def test_authorized_repair_creates_verified_vpc_and_retains_stable_until_health_passes():
    controller, api, probe, _, repair, _, _, _ = repair_fixture()
    plan = controller.build_plan(source_input(requested_env_changes={"LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED": "true"}), network_repair=repair)
    assert api.update_specs == []
    assert not plan.stable_vpc_conf.vpc_id
    assert plan.desired_vpc_conf.vpc_id == "vpc-5l5vzkr2"
    result = controller.execute(plan, promotion="full")
    assert result["state"] == "VERIFIED"
    assert api.update_specs[0].vpc_conf == plan.desired_vpc_conf
    assert api.events.index("search_cls_logs") < api.events.index("release_flow:5")
    assert probe.db_calls == 21
    assert "private" not in str(plan.safe_dict())


@pytest.mark.parametrize("kind,code", [
    ("nonempty", "VPC_REPAIR_NOT_MISSING"),
    ("reference_failed", "VPC_REPAIR_REFERENCE_INVALID"),
    ("reference_partial", "VPC_REPAIR_REFERENCE_INVALID"),
    ("database_vpc", "VPC_REPAIR_DATABASE_MISMATCH"),
    ("database_host", "VPC_REPAIR_DATABASE_MISMATCH"),
    ("database_stopped", "VPC_REPAIR_DATABASE_MISMATCH"),
    ("subnet_cidr", "VPC_REPAIR_NETWORK_MISMATCH"),
    ("subnet_vpc", "VPC_REPAIR_NETWORK_MISMATCH"),
    ("subnet_region", "VPC_REPAIR_NETWORK_MISMATCH"),
])
def test_repair_rejects_unverified_network_before_any_write(kind, code):
    controller, api, _, source, repair, reference, database, network = repair_fixture()
    if kind == "nonempty": api.stable["VpcConf"]["VpcId"] = "vpc-existing"
    if kind == "reference_failed": reference["Status"] = "failed"
    if kind == "reference_partial": reference["VpcConf"]["VpcCIDR"] = ""
    if kind == "database_vpc": database["Data"]["NetInfo"]["VpcId"] = "vpc-other"
    if kind == "database_host": database["Data"]["NetInfo"]["PubNetAddress"] = "other.example.test"
    if kind == "database_stopped": database["Data"]["DbInfo"]["Status"] = "paused"
    if kind == "subnet_cidr": network["SubnetSet"][0]["CidrBlock"] = "10.0.0.0/24"
    if kind == "subnet_vpc": network["SubnetSet"][0]["VpcId"] = "vpc-other"
    if kind == "subnet_region": network["SubnetSet"][0]["Zone"] = "ap-beijing-1"
    with pytest.raises(release.ReleaseFailure) as error:
        controller.build_plan(source_input(), network_repair=repair)
    assert error.value.code == code
    assert source.calls == 0 and api.update_specs == [] and api.targeted_calls == []


@pytest.mark.parametrize("overrides", [{"approval_ref": None}, {"artifact_mode": "stable-image"},
    {"requested_env_changes": {"IDENTITY_ADMIN_WRITES_ENABLED": "true"}}])
def test_repair_does_not_expand_authorization(overrides):
    controller, api, _, _, repair, *_ = repair_fixture()
    with pytest.raises(release.ReleaseFailure, match="explicit authorization"):
        controller.build_plan(source_input(**overrides), network_repair=repair)
    assert api.update_specs == []


def test_network_drift_after_upload_prevents_candidate_creation():
    controller, api, _, _, repair, _, database, _ = repair_fixture()
    plan = controller.build_plan(source_input(), network_repair=repair)
    database["Data"]["NetInfo"]["VpcId"] = "vpc-drift"
    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan)
    assert error.value.code == "VPC_REPAIR_DATABASE_MISMATCH"
    assert api.update_specs == []


def test_failed_candidate_never_receives_normal_traffic_during_repair():
    controller, api, probe, _, repair, *_ = repair_fixture()
    plan = controller.build_plan(source_input(), network_repair=repair)
    probe.fail_db_at = 4
    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan, promotion="full")
    assert error.value.code == "CANDIDATE_DATABASE_NOT_READY"
    assert api.flow_calls == [0]


def test_monthly_flag_must_be_read_back_before_any_candidate_route():
    controller, api, _, _, repair, *_ = repair_fixture()
    plan = controller.build_plan(source_input(requested_env_changes={"LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED": "true"}), network_repair=repair)
    create = api.create_candidate
    def omit_environment(spec):
        task = create(spec)
        api.candidate["EnvParams"] = api.stable["EnvParams"]
        return task
    api.create_candidate = omit_environment
    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan, promotion="full")
    assert error.value.code == "CANDIDATE_ENV_MISMATCH"
    assert api.targeted_calls == [] and api.flow_calls == []


def test_stable_environment_drift_prevents_candidate_creation():
    controller, api, _, _, repair, *_ = repair_fixture()
    plan = controller.build_plan(source_input(), network_repair=repair)
    api.stable["EnvParams"] = api.stable["EnvParams"].replace('"DATABASE_URL"', '"EXTRA":"changed","DATABASE_URL"')
    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan)
    assert error.value.code == "STABLE_ENV_CHANGED"
    assert api.update_specs == []


def test_full_promotion_waits_for_release_order_to_end():
    controller, api, _, _, repair, *_ = repair_fixture()
    plan = controller.build_plan(source_input(), network_repair=repair)
    original = api.release_flow
    def flow(stable, candidate, percent):
        original(stable, candidate, percent)
        if percent == 100:
            api.release_order["IsReleasing"] = True
    api.release_flow = flow
    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan, promotion="full")
    assert error.value.code == "FLOW_PROMOTION_NOT_CONFIRMED"
    assert api.flow_calls == [5, 100, 0]
