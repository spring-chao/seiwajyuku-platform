from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from app.core.settings import get_settings


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("密码至少需要10位")
    salt = os.urandom(16)
    iterations, lanes, memory_cost = 3, 4, 64 * 1024
    derived = Argon2id(
        salt=salt,
        length=32,
        iterations=iterations,
        lanes=lanes,
        memory_cost=memory_cost,
    ).derive(password.encode("utf-8"))
    return f"argon2id${iterations}${lanes}${memory_cost}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, lanes, memory_cost, salt, expected = encoded.split("$", 5)
        if scheme != "argon2id":
            return False
        derived = Argon2id(
            salt=_unb64(salt),
            length=len(_unb64(expected)),
            iterations=int(iterations),
            lanes=int(lanes),
            memory_cost=int(memory_cost),
        ).derive(password.encode("utf-8"))
        return hmac.compare_digest(derived, _unb64(expected))
    except (TypeError, ValueError):
        return False


def create_token(
    subject: int,
    token_version: int,
    token_type: str,
    expires_delta: timedelta,
    *,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(subject),
        "ver": token_version,
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    if extra_claims:
        reserved = set(payload).intersection(extra_claims)
        if reserved:
            raise ValueError(f"令牌扩展字段不能覆盖保留字段: {sorted(reserved)}")
        payload.update(extra_claims)
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}.{_b64(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = hmac.new(settings.jwt_secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        header, payload, signature = token.split(".")
        signing_input = f"{header}.{payload}"
        expected = hmac.new(
            settings.jwt_secret.encode(), signing_input.encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise ValueError("签名无效")
        data = json.loads(_unb64(payload))
        if data.get("typ") != expected_type:
            raise ValueError("令牌类型无效")
        if int(data.get("exp", 0)) <= int(datetime.now(UTC).timestamp()):
            raise ValueError("令牌已过期")
        return data
    except Exception as exc:
        raise ValueError("无效令牌") from exc


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
