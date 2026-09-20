from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
    TencentCloudSDKException,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import deploy_cloudrun_api as release


class RaisingTcbrClient:
    def __init__(self, error: TencentCloudSDKException) -> None:
        self.error = error

    def ReleaseGray(self, _request):
        raise self.error


def make_sdk_api(error: TencentCloudSDKException) -> release.TencentCloudSdkApi:
    api = object.__new__(release.TencentCloudSdkApi)
    api._tcbr = RaisingTcbrClient(error)
    api._credential_mode = "TEMPORARY"
    api._token_present = True
    return api


def test_sdk_failure_with_request_id_preserves_safe_metadata() -> None:
    api = make_sdk_api(
        TencentCloudSDKException(
            "InvalidParameter",
            "SecretId=secret-id SecretKey=secret-key Token=runtime-token "
            "Authorization: Bearer signed-value "
            "https://example.test/?signature=query-secret",
            "req-r2f-001",
        )
    )

    with pytest.raises(release.ReleaseFailure) as error:
        api._tcbr_request("ReleaseGray", object())

    assert error.value.code == "TENCENT_API_ERROR"
    evidence = error.value.evidence
    assert evidence["action"] == "ReleaseGray"
    assert evidence["tencent_error_code"] == "InvalidParameter"
    assert evidence["request_id_present"] is True
    assert evidence["request_id"] == "req-r2f-001"
    assert evidence["request_reached_service"] is True
    assert evidence["diagnostic_classification"] == "RELEASE_GRAY_PARAMETER_REJECTED"
    assert evidence["credential_mode"] == "TEMPORARY"
    assert evidence["token_present"] is True
    rendered = json.dumps(evidence, ensure_ascii=False)
    for secret in ("secret-id", "secret-key", "runtime-token", "signed-value", "query-secret"):
        assert secret not in rendered
    assert len(evidence["message_redacted"]) <= 1000


def test_sdk_failure_without_request_id_is_not_proven_reached() -> None:
    api = make_sdk_api(TencentCloudSDKException("InvalidParameter", "bad field", None))

    with pytest.raises(release.ReleaseFailure) as error:
        api._tcbr_request("ReleaseGray", object())

    evidence = error.value.evidence
    assert evidence["request_id_present"] is False
    assert evidence["request_id"] is None
    assert evidence["request_reached_service"] is False
    assert evidence["diagnostic_classification"] == "REQUEST_NOT_PROVEN_REACHED_SERVICE"


def test_resource_unavailable_is_classified_without_starting_candidate() -> None:
    api = make_sdk_api(
        TencentCloudSDKException(
            "ResourceUnavailable", "candidate version is unavailable", "req-r2f-002"
        )
    )

    with pytest.raises(release.ReleaseFailure) as error:
        api._tcbr_request("ReleaseGray", object())

    assert error.value.evidence["diagnostic_classification"] == (
        "RELEASE_GRAY_RESOURCE_UNAVAILABLE"
    )


def test_targeted_release_payload_fixture_redacts_runtime_token() -> None:
    request = release.build_targeted_release_request(
        "seiwajyuku-platform-api-253",
        "seiwajyuku-platform-api-257",
        "real-runtime-token-must-not-escape",
    )

    payload = release._redacted_targeted_release_payload(request)

    assert payload["EnvId"] == release.ENV_ID
    assert payload["ServerName"] == release.SERVICE_NAME
    assert payload["GrayType"] == "gray"
    assert payload["TrafficType"] == "URL_PARAMS"
    assert payload["GrayFlowRatio"] == 0
    assert payload["VersionFlowItems"] == [
        {
            "VersionName": "seiwajyuku-platform-api-253",
            "IsDefaultPriority": True,
            "FlowRatio": 100,
            "UrlParam": None,
            "Priority": 1,
        },
        {
            "VersionName": "seiwajyuku-platform-api-257",
            "IsDefaultPriority": False,
            "FlowRatio": 0,
            "UrlParam": {
                "Key": "sj_canary",
                "Value": "<REDACTED_RUNTIME_TOKEN>",
            },
            "Priority": 2,
        },
    ]
    assert "real-runtime-token-must-not-escape" not in json.dumps(payload)
