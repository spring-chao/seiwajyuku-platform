from __future__ import annotations

import base64
import json
from urllib.parse import urlencode

try:
    from fastapi.testclient import TestClient

    from app.main import app
    from app.migrations import run_migrations
    from app.services.iam import seed_iam

    run_migrations()
    seed_iam()
    client = TestClient(app)
    FULL_API_AVAILABLE = True
except ModuleNotFoundError:
    client = None
    FULL_API_AVAILABLE = False


def main(event: dict, context: object) -> dict:
    method = str(event.get("httpMethod") or "GET").upper()
    path = str(event.get("path") or "/")
    if path == "/ops-api":
        path = "/"
    elif path.startswith("/ops-api/"):
        path = path.removeprefix("/ops-api")
    query = event.get("queryStringParameters") or {}
    if query:
        path = f"{path}?{urlencode(query, doseq=True)}"
    if not FULL_API_AVAILABLE:
        if method == "GET" and path.split("?", 1)[0] == "/api/v1/health":
            return {
                "statusCode": 200,
                "headers": {
                    "content-type": "application/json; charset=utf-8",
                    "access-control-allow-origin": "*",
                },
                "body": json.dumps(
                    {
                        "status": "ok",
                        "environment": "cloudbase-readonly-probe",
                        "full_api_available": False,
                        "reason": "persistent database and Python runtime dependencies are not provisioned",
                    },
                    ensure_ascii=False,
                ),
            }
        status = 403 if method not in {"GET", "HEAD", "OPTIONS"} else 503
        return {
            "statusCode": status,
            "headers": {
                "content-type": "application/json; charset=utf-8",
                "access-control-allow-origin": "*",
            },
            "body": json.dumps(
                {
                    "detail": "当前仅为 CloudBase 只读可行性探针，完整 API 尚未启用"
                },
                ensure_ascii=False,
            ),
        }
    headers = {
        str(key): str(value)
        for key, value in (event.get("headers") or {}).items()
        if str(key).lower() not in {"host", "content-length"}
    }
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body)
    response = client.request(method, path, headers=headers, content=body)
    response_headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "transfer-encoding", "connection"}
    }
    return {
        "statusCode": response.status_code,
        "headers": response_headers,
        "isBase64Encoded": False,
        "body": response.text,
    }
