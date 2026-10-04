from dataclasses import replace
import json

import pytest
from test_cloudrun_vpc_repair import repair_fixture
from test_cloudrun_release_network_guard import source_input
import deploy_cloudrun_api as release


def endpoint_fixture():
    controller, api, probe, source, repair, reference, database, network = repair_fixture()
    api.stable["EnvParams"] = json.dumps({"DATABASE_URL": "mysql+pymysql://user:credential%40test@db.public.test:4400/db?charset=utf8mb4", "EXISTING_FLAG": "true"})
    reference["EnvParams"] = json.dumps({"DATABASE_URL": "mysql+pymysql://user:credential%40test@db.internal.test:3306/db?charset=utf8mb4"})
    database["Data"]["NetInfo"].update(PubNetAddress="db.public.test:4400", PrivateNetAddress="db.internal.test:3306")
    return controller, api, probe, source, replace(repair, restore_private_database_endpoint=True), reference, database


def test_restores_verified_endpoint_with_identical_credentials_and_existing_flags():
    controller, api, _, _, repair, reference, _ = endpoint_fixture()
    plan = controller.build_plan(source_input(requested_env_changes={"LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED": "true"}), network_repair=repair)
    env = json.loads(plan.env_params_json)
    assert env["DATABASE_URL"] == json.loads(reference["EnvParams"])["DATABASE_URL"]
    assert env["EXISTING_FLAG"] == "true" and env["LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED"] == "true"
    assert "credential%40test" not in str(plan.safe_dict()) and "db.internal.test" not in str(plan.safe_dict())
    assert plan.safe_dict()["network_repair"]["database_endpoint_restored"] is True
    assert controller.execute(plan, promotion="full")["state"] == "VERIFIED"
    assert api.events.index("search_cls_logs") < api.events.index("release_flow:5")


@pytest.mark.parametrize("change", ["username", "password", "database", "driver", "query", "endpoint", "port", "metadata", "stable_port"])
def test_unproven_database_or_credential_change_is_rejected_before_upload(change):
    controller, api, _, source, repair, reference, database = endpoint_fixture()
    value = json.loads(reference["EnvParams"])["DATABASE_URL"]
    if change == "username": value = value.replace("//user:", "//other:")
    elif change == "password": value = value.replace("credential%40test", "other-secret")
    elif change == "database": value = value.replace("/db?", "/other?")
    elif change == "driver": value = value.replace("mysql+pymysql", "mysql")
    elif change == "query": value = value.replace("utf8mb4", "latin1")
    elif change == "endpoint": value = value.replace("db.internal.test", "other.internal.test")
    elif change == "port": value = value.replace(":3306", ":3307")
    elif change == "metadata": database["Data"]["NetInfo"]["PrivateNetAddress"] = "other.internal.test:3306"
    elif change == "stable_port": api.stable["EnvParams"] = api.stable["EnvParams"].replace(":4400", ":4401")
    reference["EnvParams"] = json.dumps({"DATABASE_URL": value})
    with pytest.raises(release.ReleaseFailure) as error:
        controller.build_plan(source_input(), network_repair=repair)
    assert error.value.code == "VPC_REPAIR_DATABASE_ENDPOINT_MISMATCH"
    assert source.calls == 0 and api.update_specs == [] and api.flow_calls == []


def test_private_endpoint_drift_after_upload_prevents_candidate_creation():
    controller, api, _, source, repair, reference, database = endpoint_fixture()
    plan = controller.build_plan(source_input(), network_repair=repair)
    database["Data"]["NetInfo"]["PrivateNetAddress"] = "db.new.internal.test:3306"
    reference["EnvParams"] = reference["EnvParams"].replace("db.internal.test", "db.new.internal.test")
    with pytest.raises(release.ReleaseFailure) as error:
        controller.execute(plan, promotion="full")
    assert error.value.code == "VPC_REPAIR_DATABASE_ENDPOINT_CHANGED"
    assert api.update_specs == [] and api.flow_calls == []


def test_callers_cannot_supply_database_url_through_repair_environment():
    controller, api, _, source, repair, *_ = endpoint_fixture()
    with pytest.raises(release.ReleaseFailure) as error:
        controller.build_plan(source_input(requested_env_changes={"DATABASE_URL": "caller-controlled"}), network_repair=repair)
    assert error.value.code == "VPC_REPAIR_SCOPE_INVALID"
    assert source.calls == 0 and api.update_specs == []


def test_already_matching_private_endpoint_remains_unchanged():
    controller, api, _, _, repair, reference, _ = endpoint_fixture()
    api.stable["EnvParams"] = reference["EnvParams"]
    plan = controller.build_plan(source_input(), network_repair=repair)
    assert plan.env_params_json is None
    assert controller.execute(plan, promotion="full")["state"] == "VERIFIED"
