from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from app.services.wechat_identity import WeChatProviderError, exchange_wechat_code


def _mock_client(response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = response
    return client


@pytest.mark.parametrize(
    ("errcode", "message"),
    [
        (40029, "微信登录凭证已失效，请重新点击绑定"),
        (40163, "微信登录凭证已使用，请重新点击绑定"),
        (40013, "微信小程序身份配置无效，请联系工作人员"),
        (40125, "微信小程序身份配置无效，请联系工作人员"),
        (40164, "微信小程序服务器 IP 未加入白名单，请联系工作人员"),
        (45011, "微信登录请求过于频繁，请稍后再试"),
    ],
)
def test_wechat_provider_error_is_safe_and_actionable(
    errcode: int, message: str
) -> None:
    response = MagicMock(status_code=200)
    response.json.return_value = {"errcode": errcode, "errmsg": "provider detail"}
    with patch.dict(
        os.environ,
        {
            "APP_ENV": "test",
            "WECHAT_LOCAL_TEST_MODE": "false",
            "WECHAT_MINIPROGRAM_APP_ID": "test-app-id",
            "WECHAT_MINIPROGRAM_APP_SECRET": "test-app-secret",
        },
    ), patch(
        "app.services.wechat_identity.httpx.Client", return_value=_mock_client(response)
    ):
        with pytest.raises(WeChatProviderError, match=message):
            exchange_wechat_code("fresh-login-code")


def test_wechat_provider_success_returns_only_safe_identity_fields() -> None:
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "openid": "openid-for-test",
        "session_key": "session-key-for-test",
    }
    with patch.dict(
        os.environ,
        {
            "APP_ENV": "test",
            "WECHAT_LOCAL_TEST_MODE": "false",
            "WECHAT_MINIPROGRAM_APP_ID": "test-app-id",
            "WECHAT_MINIPROGRAM_APP_SECRET": "test-app-secret",
        },
    ), patch(
        "app.services.wechat_identity.httpx.Client", return_value=_mock_client(response)
    ):
        assert exchange_wechat_code("fresh-login-code") == {
            "appid": "test-app-id",
            "openid": "openid-for-test",
        }
