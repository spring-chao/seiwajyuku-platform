"""Five-minute, single-purpose capability; only signin can commit the fact."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from app.core.settings import get_settings


def _base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def create_ticket(event_id: str, member: dict, *, binding_id: int, token_version: int) -> str:
    issued_at = int(time.time())
    payload = {"purpose": "MEMBER_CHECKIN", "event_id": event_id, "member": member,
               "iat": issued_at, "exp": issued_at + 300, "binding_id": binding_id,
               "token_version": token_version}
    encoded = _base64(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    signature = _base64(hmac.new(get_settings().signin_platform_api_key.encode(), encoded.encode(), hashlib.sha256).digest())
    return encoded + "." + signature
