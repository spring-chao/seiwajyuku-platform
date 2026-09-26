"""Independent Phase 3 smoke. Does not import or open an envelope journal."""

from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path

from r3_cloud_read import SCOPE, TencentCloudReadAdapter, collect_cloud_runtime
from r3_read_evidence import ReadFailure, _Issuer, verify_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--collect-readonly-evidence", action="store_true", required=True
    )
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--runtime-commit", required=True)
    parser.add_argument("--manage-task-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    key = secrets.token_bytes(32)
    secret_id, secret_key = (
        os.getenv("TENCENTCLOUD_SECRET_ID"),
        os.getenv("TENCENTCLOUD_SECRET_KEY"),
    )
    if not secret_id or not secret_key:
        print(
            json.dumps(
                {
                    "result": "NOT_RUN",
                    "reason": "CREDENTIALS_NOT_CONFIGURED",
                    "production_ready": False,
                }
            )
        )
        return 2
    if args.output.exists() or args.output.with_suffix(".verifier-key").exists():
        parser.error("output/key already exists; use a new collection path")
    issuer = _Issuer(key)
    try:
        cloud = TencentCloudReadAdapter.authenticated(
            issuer, secret_id, secret_key, os.getenv("TENCENTCLOUD_TOKEN")
        )
        bundle = collect_cloud_runtime(
            cloud,
            issuer,
            args.baseline_revision,
            args.manage_task_id,
            args.runtime_commit,
        )
        verify_bundle(
            bundle,
            key=key,
            expected_scope=SCOPE,
            expected_revision=args.baseline_revision,
        )
    except ReadFailure as exc:
        print(
            json.dumps(
                {
                    "result": "FAIL_CLOSED",
                    "code": exc.code,
                    "error_code": exc.error_code,
                    "request_id": exc.request_id,
                    "attempt_count": exc.attempt_count,
                    "production_ready": False,
                }
            )
        )
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Local verification key is NOT part of the evidence bundle/report/journal.
    # Keep it in an owner-restricted output directory, never commit/upload it.
    key_path = args.output.with_suffix(".verifier-key")
    fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                "result": "PASS",
                "production_ready": False,
                "control_plane_read": "PASS",
                "runtime_read": "PASS",
                "production_db_read": "NOT_AUTHORIZED",
                "bundle": str(args.output),
                "bundle_fingerprint": bundle["bundle_fingerprint"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
