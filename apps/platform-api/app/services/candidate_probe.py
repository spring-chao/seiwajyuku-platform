"""Read-only HTTP self-check in the exact candidate process, before public flow."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import socket
import time
from urllib.request import urlopen

PROBE_ENV = "DEPLOYMENT_CANDIDATE_PROBE_ID"
RESULT_MARKER = "CANDIDATE_PROBE_RESULT "
logger = logging.getLogger("uvicorn.error")


def configured_id() -> str | None:
    value = os.environ.get(PROBE_ENV, "")
    return value if re.fullmatch(r"[A-Za-z0-9_-]{32,96}", value) else None


def result_id(probe_id: str) -> str:
    return "sj_result_" + hashlib.sha256(probe_id.encode()).hexdigest()


def _get(path: str, probe_id: str) -> dict:
    # No caller-selected host, port, path, headers or credentials.
    with urlopen(f"http://127.0.0.1:8000{path}?sj_probe={probe_id}", timeout=5) as response:
        if response.status != 200:
            raise RuntimeError("non-success candidate response")
        return json.load(response)


def check(probe_id: str, *, get=_get, wait_for_socket=True) -> dict:
    from app.core.build_info import get_build_info
    proof = {"id": result_id(probe_id), "status": "failed", "commit_sha": get_build_info()["commit_sha"],
             "loopback": "127.0.0.1:8000", "http_requests": 0, "database_health_passed": 0}
    try:
        if wait_for_socket:
            for attempt in range(60):
                try:
                    with socket.create_connection(("127.0.0.1", 8000), timeout=1):
                        break
                except OSError:
                    if attempt == 59: raise RuntimeError("candidate listener unavailable")
                    time.sleep(1)
        first = get("/api/v1/system/build-info", probe_id)
        proof["http_requests"] += 1
        if first.get("commit_sha") != proof["commit_sha"]:
            raise RuntimeError("candidate build identity mismatch")
        live = get("/health/live", probe_id)
        proof["http_requests"] += 1
        if live.get("status") != "ok": raise RuntimeError("candidate liveness failed")
        for _ in range(20):
            health = get("/api/v1/health", probe_id)
            proof["http_requests"] += 1
            if health.get("status") != "ok": raise RuntimeError("candidate database health failed")
            proof["database_health_passed"] += 1
        last = get("/api/v1/system/build-info", probe_id)
        proof["http_requests"] += 1
        if last.get("commit_sha") != proof["commit_sha"]:
            raise RuntimeError("candidate build identity changed")
        proof["status"] = "passed"
    except Exception as error:
        # Never log response bodies, database credentials or exception text.
        proof["error_type"] = type(error).__name__
    logger.info(RESULT_MARKER + "%s", json.dumps(proof, sort_keys=True))
    return proof


async def run(probe_id: str) -> None:
    await asyncio.to_thread(check, probe_id)
