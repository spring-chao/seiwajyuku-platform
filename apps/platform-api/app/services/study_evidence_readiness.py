"""Candidate-only synthetic photo check; never creates a learning meeting."""
from __future__ import annotations

import io
from urllib.error import HTTPError
from urllib.request import urlopen

from PIL import Image

from app.core.settings import get_settings
from app.services.study_evidence_storage import EvidenceStorage, _missing_object_error


def _anonymous_status(url: str) -> int:
    try:
        with urlopen(url, timeout=10) as response:
            return response.status
    except HTTPError as error:
        return error.code


def check(*, storage_factory=EvidenceStorage, anonymous_status=_anonymous_status) -> dict:
    """Put/Get/Delete only one generated private object, with compensation.

    Called inside a source candidate before normal traffic is permitted. No
    caller-selected keys, no business rows, and no credentials/URLs in proof.
    """
    proof = {"status": "failed", "private": False, "put": False, "get": False,
             "delete": False, "deleted_verified": False}
    storage = None
    key = None
    attempted = False
    try:
        settings = get_settings()
        if not settings.study_evidence_cleanup_enabled or len(settings.study_evidence_cleanup_token) < 32:
            raise RuntimeError("cleanup configuration unavailable")
        storage = storage_factory()
        if storage.backend != "cloudbase":
            raise RuntimeError("private CloudBase storage required")
        proof["credential_mode"] = storage.credential_mode
        image = io.BytesIO()
        Image.new("RGB", (32, 24), "white").save(image, "JPEG")
        content = image.getvalue()
        key = storage.make_key(session_id=0, extension="jpg")
        attempted = True
        storage.put(key, content, "image/jpeg")
        proof["put"] = True
        proof["get"] = storage.get(key) == content
        url = f"https://{storage.bucket}.cos.{storage.region}.myqcloud.com/{key}"
        proof["private"] = anonymous_status(url) in {403, 404}
        if not proof["get"] or not proof["private"]:
            raise RuntimeError("private object verification failed")
        storage.delete(key)
        proof["delete"] = True
        try:
            storage.client.head_object(Bucket=storage.bucket, Key=key)
        except Exception as error:
            if not _missing_object_error(error):
                raise
            proof["deleted_verified"] = True
        if not proof["deleted_verified"]:
            raise RuntimeError("synthetic object still exists")
        proof["status"] = "passed"
    except Exception as error:
        proof["error_type"] = type(error).__name__
    finally:
        if attempted and not proof["delete"]:
            try:
                storage.delete(key)
                proof["delete"] = True
            except Exception:
                proof["compensation_failed"] = True
                # This is exclusively our random synthetic key, never a
                # learner's photo. Retain it only when recovery is necessary.
                proof["synthetic_key"] = key
    return proof
