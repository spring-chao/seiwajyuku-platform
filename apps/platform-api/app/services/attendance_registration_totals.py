"""Read registration denominators from signin, separately from synced check-in facts.

An immediate check-in delivery can contain just one signed registration. Such
rows must not be presented as the complete registration roster. No source rows
are persisted here; the short-lived cache holds aggregate counts only.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from threading import Lock
from time import monotonic

import httpx

from app.core.settings import get_settings

_cache: dict[tuple[str, str, str], tuple[float, int | None]] = {}
_lock = Lock()


def _registration_count(client, base: str, key: str, session_id: str) -> int | None:
    cache_key = (base, hashlib.sha256(key.encode()).hexdigest(), session_id)
    with _lock:
        cached = _cache.get(cache_key)
        if cached and cached[0] > monotonic():
            return cached[1]
    total = None
    try:
        ids: set[str] = set()
        cursor = None
        seen_cursors: set[str] = set()
        for _ in range(20):
            params = {"session_id": session_id, "limit": 500}
            if cursor:
                params["cursor"] = cursor
            response = client.get(base + "/ops/v1/attendance/records", params=params,
                                  headers={"X-API-Key": key})
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get("ok") is False or not isinstance(data.get("items"), list):
                break
            for row in data["items"]:
                record_id = str(row.get("external_registration_id") or row.get("external_record_id") or "")
                if not record_id:
                    raise ValueError("source registration identity missing")
                ids.add(record_id)
            cursor = data.get("next_cursor")
            if not cursor:
                if data.get("has_more"):
                    break
                total = len(ids)
                break
            if cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        # A partial roster is not a confirmed denominator. Do not log source
        # bodies, names, authorization headers or credential-bearing errors.
        total = None
    with _lock:
        if len(_cache) >= 1000:
            _cache.clear()
        _cache[cache_key] = (monotonic() + (30 if total is not None else 3), total)
    return total


def attach_registration_counts(rows: list[dict]) -> None:
    """Caller must scope rows to the user's organizations before this read."""
    settings = get_settings()
    base = settings.signin_api_base_url.rstrip("/")
    key = settings.signin_service_api_key
    source_ids = set()
    for row in rows:
        row["registration_count"] = None
        row["registration_count_status"] = "UNAVAILABLE"
        if row.get("source_key") != "signin":
            row["registration_count"] = row.get("record_count", 0)
            row["registration_count_status"] = "LOCAL_RECORDS"
        elif row.get("external_session_id"):
            source_ids.add(str(row["external_session_id"]))
    if not source_ids or not base or not key:
        return
    # All requests are machine-to-machine reads. No sync or score writes occur.
    with httpx.Client(timeout=8, follow_redirects=False) as client:
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = sorted(source_ids)
            counts = dict(zip(ids, pool.map(lambda value: _registration_count(client, base, key, value), ids)))
    for row in rows:
        if row.get("source_key") == "signin":
            count = counts.get(str(row.get("external_session_id") or ""))
            row["registration_count"] = count
            row["registration_count_status"] = "CONFIRMED" if count is not None else "UNAVAILABLE"
