"""Safe, read-only build provenance exposed by the API.

Only explicitly allow-listed provenance fields are returned.  Runtime secrets,
database connection strings, and other environment variables are deliberately
not part of this module.
"""

from __future__ import annotations

import os
import json
from functools import lru_cache
from pathlib import Path
from typing import Final

from app.core.settings import get_settings


UNKNOWN: Final = "unknown"


@lru_cache(maxsize=1)
def _baked_build_info() -> dict[str, str]:
    """Source deployments may not pass Docker build args. Read their sealed stamp."""
    path = Path(__file__).resolve().parents[1] / "build-info.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    allowed = {"version", "commit_sha", "build_time_utc", "build_id"}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items()
            if key in allowed and isinstance(value, str) and value and value != UNKNOWN}


def _value(*names: str) -> str:
    """Return the first non-empty provenance value from the runtime environment."""

    for name in names:
        value = os.getenv(name, "").strip()
        if value and value != UNKNOWN:
            return value
    return UNKNOWN


def get_build_info() -> dict[str, str]:
    """Return the stable, non-sensitive build identity of this API process.

    Production deployment should inject the ``APP_*`` values from the
    immutable build/deployment manifest.  ``GITHUB_*`` fallbacks are useful for
    CI and local diagnostics, while missing values remain explicitly marked as
    ``unknown`` instead of being guessed from mutable runtime state.
    """

    result = {
        "version": _value("APP_VERSION", "APP_RELEASE_VERSION"),
        "commit_sha": _value("APP_GIT_SHA", "GITHUB_SHA"),
        "build_time_utc": _value("APP_BUILD_TIME_UTC", "APP_BUILD_TIME"),
        "build_id": _value("APP_BUILD_ID", "GITHUB_RUN_ID"),
        "image_digest": _value("APP_IMAGE_DIGEST", "IMAGE_DIGEST"),
        "environment": get_settings().app_env,
    }
    # The source stamp wins over inherited environment metadata from an older
    # revision. The image digest is supplied separately by the deployment tool.
    result.update(_baked_build_info())
    return result
