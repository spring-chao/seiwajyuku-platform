import io
import json
from unittest.mock import MagicMock
from urllib.error import HTTPError

import pytest

from app.services.study_evidence_bridge import BridgeObjectError, NoRedirect, StorageBridge
from app.services.study_evidence_storage import EvidenceStorage, EvidenceStorageError

URL = "https://shengheshu-d2g2zyyl99f6c6fc2-1453587887.ap-shanghai.app.tcloudbase.com/study-evidence-storage"
TOKEN = "synthetic-server-token-" * 3
KEY = "study-meetings/production/2026/10/" + "a" * 32 + ".jpg"
HOST = "7368-synthetic-123.cos.ap-shanghai.myqcloud.com"


def grant(headers=None):
    return {"url": f"https://{HOST}/{KEY}?q-signature=synthetic-signed-value",
            "headers": headers or {}, "expires_in": 90}


def storage_bridge(*responses):
    bridge = StorageBridge(URL, TOKEN, "7368-synthetic-123", "ap-shanghai")
    bridge.opener = MagicMock()
    bridge.opener.open.side_effect = [io.BytesIO(json.dumps(response).encode())
        if isinstance(response, dict) else response for response in responses]
    return bridge


def test_server_only_grant_then_binary_private_put_and_get():
    headers = {"content-type": "image/jpeg", "x-cos-acl": "private", "if-none-match": "*"}
    bridge = storage_bridge(grant(headers), io.BytesIO(), grant(), io.BytesIO(b"photo"))
    bridge.request("PUT", KEY, b"photo", "image/jpeg")
    assert bridge.request("GET", KEY) == b"photo"
    calls = bridge.opener.open.call_args_list
    token_request, put_request = (c.args[0] for c in calls[:2])
    assert token_request.full_url == URL and token_request.get_method() == "POST"
    assert dict(token_request.header_items())["X-study-evidence-cleanup-token"] == TOKEN
    assert json.loads(token_request.data) == {"method": "PUT", "key": KEY, "content_type": "image/jpeg"}
    assert put_request.data == b"photo" and put_request.get_method() == "PUT"
    assert dict(put_request.header_items())["X-cos-acl"] == "private"
    assert dict(put_request.header_items())["If-none-match"] == "*"
    assert TOKEN not in str(put_request.header_items())


@pytest.mark.parametrize("url,token", [
    (URL.replace("https:", "http:"), TOKEN),
    (URL.replace(".app.tcloudbase.com", ".evil.invalid"), TOKEN),
    (URL + "/other", TOKEN), (URL + "?token=x", TOKEN),
    (URL, "short"),
])
def test_invalid_bridge_configuration_is_rejected(url, token):
    with pytest.raises(ValueError, match="configuration"):
        StorageBridge(url, token, "7368-synthetic-123", "ap-shanghai")


@pytest.mark.parametrize("change", [
    {"url": "https://evil.invalid/" + KEY},
    {"url": f"https://{HOST}/other.jpg"},
    {"url": f"http://{HOST}/{KEY}"},
    {"headers": {"x-cos-acl": "public-read"}}, {"expires_in": 3600},
])
def test_invalid_object_grant_never_sends_the_object(change):
    bridge = storage_bridge({**grant(), **change})
    with pytest.raises(ValueError):
        bridge.request("GET", KEY)
    assert bridge.opener.open.call_count == 1


def test_redirects_are_rejected_and_gateway_failures_are_not_object_misses():
    assert NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid") is None
    gateway_error = HTTPError(URL, 404, "missing bridge", {}, None)
    bridge = storage_bridge(gateway_error)
    with pytest.raises(ValueError, match="bridge unavailable") as error:
        bridge.request("HEAD", KEY)
    assert not isinstance(error.value, BridgeObjectError)
    assert URL not in str(error.value)
    signed_error = HTTPError(grant()["url"], 404, "sensitive provider detail", {}, None)
    bridge = storage_bridge(grant(), signed_error)
    with pytest.raises(BridgeObjectError) as error:
        bridge.request("HEAD", KEY)
    assert error.value.status_code == 404
    assert "synthetic-signed-value" not in str(error.value)
    assert "sensitive provider" not in str(error.value)
    assert error.value.__cause__ is None


def test_cloudbase_fallback_preserves_namespace_and_checks_scoped_keys(monkeypatch):
    for name in ("TENCENTCLOUD_SECRETID", "TENCENTCLOUD_SECRET_ID", "TENCENTCLOUD_SECRETKEY",
                 "TENCENTCLOUD_SECRET_KEY", "CLOUDBASE_STORAGE_SECRET_ID", "CLOUDBASE_STORAGE_SECRET_KEY",
                 "STUDY_EVIDENCE_COS_SECRET_ID", "STUDY_EVIDENCE_COS_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {"APP_ENV": "production", "STUDY_EVIDENCE_STORAGE_BACKEND": "cloudbase",
                        "CLOUDBASE_STORAGE_BUCKET": "7368-synthetic-123", "CLOUDBASE_STORAGE_REGION": "ap-shanghai",
                        "CLOUDBASE_STORAGE_PREFIX": "study-meetings/", "CLOUDBASE_STORAGE_BRIDGE_URL": URL,
                        "STUDY_EVIDENCE_CLEANUP_TOKEN": TOKEN}.items():
        monkeypatch.setenv(name, value)
    storage = EvidenceStorage()
    assert storage.credential_mode == "cloudbase-function"
    assert storage.namespace == "ap-shanghai/7368-synthetic-123/study-meetings/"
    storage.bridge = MagicMock()
    storage.put(KEY, b"photo", "image/jpeg")
    storage.bridge.request.assert_called_once_with("PUT", KEY, b"photo", "image/jpeg")
    with pytest.raises(EvidenceStorageError, match="路径"):
        storage.get(KEY.replace("production", "test"))
    storage.bridge.request.side_effect = BridgeObjectError(404)
    assert storage.exists(KEY) is False
    storage.delete(KEY)  # Already removed objects remain idempotent.
    for failure in (BridgeObjectError(403), ValueError("bridge unavailable")):
        storage.bridge.request.side_effect = failure
        with pytest.raises(EvidenceStorageError):
            storage.exists(KEY)
    monkeypatch.setenv("CLOUDBASE_STORAGE_BRIDGE_URL", "https://evil.invalid")
    with pytest.raises(EvidenceStorageError, match="接入配置无效"):
        EvidenceStorage()
