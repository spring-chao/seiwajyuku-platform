from __future__ import annotations

import base64
import hashlib
import hmac
import re

from cryptography.fernet import Fernet, InvalidToken

from app.core.settings import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    if settings.field_encryption_key:
        key = settings.field_encryption_key.encode()
    else:
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.jwt_secret.encode()).digest())
    return Fernet(key)


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) != 11 or not digits.startswith("1"):
        raise ValueError("手机号必须为11位中国大陆号码")
    return digits


def mask_phone(phone: str) -> str:
    digits = normalize_phone(phone)
    return f"{digits[:3]}****{digits[-4:]}"


def phone_hash(phone: str) -> str:
    settings = get_settings()
    return hmac.new(
        settings.jwt_secret.encode(), normalize_phone(phone).encode(), hashlib.sha256
    ).hexdigest()


def encrypt_text(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_text(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("敏感字段解密失败") from exc


def protected_phone(phone: str) -> dict[str, str]:
    digits = normalize_phone(phone)
    return {
        "phone_ciphertext": encrypt_text(digits),
        "phone_hash": phone_hash(digits),
        "phone_last4": digits[-4:],
        "phone_masked": mask_phone(digits),
    }

