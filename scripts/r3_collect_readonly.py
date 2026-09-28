"""Independent Phase 3 smoke. Does not import or open an envelope journal."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
from pathlib import Path

from r3_cloud_read import SCOPE, TencentCloudReadAdapter, collect_cloud_runtime
from r3_read_evidence import ReadFailure, _Issuer, verify_bundle


def _windows_trusted_acl(path):
    # Fixed script; the path is data in an environment variable, not shell code.
    script = r"""
$ErrorActionPreference = 'Stop'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$items = @($env:R3_VERIFIER_ACL_PATH, [System.IO.Path]::GetDirectoryName($env:R3_VERIFIER_ACL_PATH))
$records = @(foreach ($item in $items) {
    $acl = if ([System.IO.Directory]::Exists($item)) {
        [System.IO.Directory]::GetAccessControl($item)
    } else {
        [System.IO.File]::GetAccessControl($item)
    }
    $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) | ForEach-Object {
        @{sid=$_.IdentityReference.Value; allow=($_.AccessControlType -eq 'Allow')}
    })
    @{owner=$acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value;
      protected=$acl.AreAccessRulesProtected; rules=$rules}
})
@{identity=$identity; records=$records} | ConvertTo-Json -Depth 5 -Compress
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "R3_VERIFIER_ACL_PATH": str(path)},
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    data = json.loads(result.stdout)
    trusted = {data["identity"], "S-1-5-18", "S-1-5-32-544"}
    records = data["records"]
    if len(records) != 2 or records[1]["protected"] is not True:
        raise ReadFailure("VERIFIER_KEY_ACL_UNTRUSTED")
    for record in records:
        if record["owner"] not in trusted or not record["rules"]:
            raise ReadFailure("VERIFIER_KEY_ACL_UNTRUSTED")
        if any(
            type(r["allow"]) is not bool or not isinstance(r["sid"], str)
            for r in record["rules"]
        ):
            raise ReadFailure("VERIFIER_KEY_ACL_UNTRUSTED")
        if any(r["allow"] is True and r["sid"] not in trusted for r in record["rules"]):
            raise ReadFailure("VERIFIER_KEY_ACL_UNTRUSTED")


def _trusted_key_permissions(path):
    if os.name == "nt":
        _windows_trusted_acl(path)
    else:
        for item in (path, path.parent):
            info = item.stat()
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ReadFailure("VERIFIER_KEY_PERMISSIONS_UNTRUSTED")


def _load_verifier_key(path, output):
    if path is None:
        raise ReadFailure("VERIFIER_KEY_REQUIRED")
    try:
        original = path.absolute()
        for item in (original, *original.parents):
            info = item.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
            ):
                raise ReadFailure("VERIFIER_KEY_LINK_DISALLOWED")
        path = path.resolve(strict=True)
        output = output.resolve()
        if path == output or path.is_relative_to(output.parent):
            raise ReadFailure("VERIFIER_KEY_OUTPUT_LOCATION_DISALLOWED")
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ReadFailure("VERIFIER_KEY_FILE_INVALID")
        _trusted_key_permissions(path)
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            key = stream.read(4097)
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ReadFailure("VERIFIER_KEY_CHANGED")
        _trusted_key_permissions(path)
        if not 32 <= len(key) <= 4096:
            raise ReadFailure("VERIFIER_KEY_SIZE_INVALID")
        return key
    except ReadFailure:
        raise
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        raise ReadFailure("VERIFIER_KEY_NOT_TRUSTED") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--collect-readonly-evidence", action="store_true", required=True
    )
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--runtime-commit", required=True)
    parser.add_argument("--manage-task-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verifier-key-file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ReadFailure("BUNDLE_ALREADY_EXISTS")
        key = _load_verifier_key(args.verifier_key_file, args.output)
    except ReadFailure as exc:
        print(
            json.dumps(
                {"result": "FAIL_CLOSED", "code": exc.code, "production_ready": False}
            )
        )
        return 2
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
