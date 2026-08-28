#!/usr/bin/env python3
"""Verify a clean source checkout and create/validate a release manifest.

The command intentionally uses only Git and the Python standard library.  It
is suitable for CI and for a later, separately approved production release
job.  It never reads or prints secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _non_empty(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def verify_checkout(
    repo_root: Path,
    *,
    expected_commit: str | None = None,
    require_origin_main: bool = False,
) -> dict[str, str]:
    """Require a clean checkout and return its immutable HEAD/tree identity."""

    dirty = _git(repo_root, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        changed = "\n".join(line[:160] for line in dirty.splitlines()[:20])
        raise RuntimeError(
            "工作区必须干净才能生成发布 provenance；发现未提交变更：\n" + changed
        )

    head = _git(repo_root, "rev-parse", "HEAD").lower()
    if not SHA_RE.fullmatch(head):
        raise RuntimeError("HEAD 不是有效的 40 位 Git SHA")

    expected = _non_empty(expected_commit) or _non_empty(os.getenv("GITHUB_SHA"))
    if expected and head != expected.lower():
        raise RuntimeError(f"HEAD {head} 与期望提交 {expected} 不一致")

    if require_origin_main:
        ref = _non_empty(os.getenv("GITHUB_REF"))
        if ref and ref != "refs/heads/main":
            raise RuntimeError(f"发布 provenance 只允许 main，当前 ref 为 {ref}")
        remote_line = _git(repo_root, "ls-remote", "origin", "refs/heads/main")
        remote = remote_line.split()[0].lower() if remote_line else ""
        if not SHA_RE.fullmatch(remote):
            raise RuntimeError("无法解析 origin/main 的远端提交")
        if remote != head:
            raise RuntimeError(f"HEAD {head} 尚未与 origin/main {remote} 对齐")

    return {"release_commit": head, "source_tree_sha": _git(repo_root, "rev-parse", "HEAD^{tree}")}


def _json_object(raw: str | None, *, field_name: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{field_name} 必须是 JSON 对象") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{field_name} 必须是 JSON 对象")
    return value


def create_manifest(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = args.repo_root.resolve()
    identity = verify_checkout(
        repo_root,
        expected_commit=args.expected_commit,
        require_origin_main=args.require_origin_main,
    )
    build_id = _non_empty(args.build_id) or _non_empty(os.getenv("GITHUB_RUN_ID"))
    ci_run = _non_empty(args.ci_run) or _non_empty(os.getenv("GITHUB_RUN_ID"))
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        **identity,
        "version": _non_empty(args.version) or _non_empty(os.getenv("APP_VERSION")) or "ci",
        "environment": _non_empty(args.environment) or _non_empty(os.getenv("APP_ENV")) or "ci",
        "generated_at_utc": _non_empty(args.generated_at_utc) or _utc_now(),
        "github_ci_run": ci_run,
        "github_ci_workflow": _non_empty(args.ci_workflow) or _non_empty(os.getenv("GITHUB_WORKFLOW")),
        # A manifest generated outside a completed CI job must not be
        # accidentally presented as release-ready.  The CI job passes
        # ``--ci-conclusion success`` explicitly after all required jobs pass.
        "ci_conclusion": _non_empty(args.ci_conclusion) or "pending",
        "build_id": build_id,
        "image_tag": _non_empty(args.image_tag),
        "image_digest": _non_empty(args.image_digest),
        "cloudrun_revision": _non_empty(args.cloudrun_revision),
        "deployment_time_utc": _non_empty(args.deployment_time_utc),
        "traffic_ratio": args.traffic_ratio,
        "backup_id": _non_empty(args.backup_id),
        "feature_flags": _json_object(args.feature_flags, field_name="feature_flags"),
        "migration_state": _json_object(args.migration_state, field_name="migration_state"),
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def validate_manifest(
    path: Path,
    *,
    require_image_digest: bool = False,
    require_deployment: bool = False,
) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取发布 manifest：{path}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("发布 manifest 必须是 JSON 对象")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("不支持的发布 manifest schema_version")
    for field in ("release_commit", "source_tree_sha"):
        value = str(manifest.get(field) or "").lower()
        if not SHA_RE.fullmatch(value):
            raise RuntimeError(f"manifest.{field} 必须是 40 位 Git SHA")
    if manifest.get("ci_conclusion") != "success":
        raise RuntimeError("CI 尚未 success，禁止通过发布 provenance 门禁")
    for field in ("github_ci_run", "build_id", "image_tag"):
        if not _non_empty(str(manifest.get(field) or "")):
            raise RuntimeError(f"manifest.{field} 不能为空")
    digest = _non_empty(str(manifest.get("image_digest") or ""))
    if require_image_digest and not digest:
        raise RuntimeError("生产发布 manifest 必须包含 image_digest")
    if digest and not DIGEST_RE.fullmatch(digest.lower()):
        raise RuntimeError("manifest.image_digest 必须是 sha256 digest")
    if require_deployment:
        for field in ("cloudrun_revision", "deployment_time_utc", "backup_id"):
            if not _non_empty(str(manifest.get(field) or "")):
                raise RuntimeError(f"生产发布 manifest.{field} 不能为空")
        traffic_ratio = manifest.get("traffic_ratio")
        if not isinstance(traffic_ratio, int) or not 0 <= traffic_ratio <= 100:
            raise RuntimeError("生产发布 manifest.traffic_ratio 必须是0到100之间的整数")
    for field in ("feature_flags", "migration_state"):
        if not isinstance(manifest.get(field), dict):
            raise RuntimeError(f"manifest.{field} 必须是 JSON 对象")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify", help="验证 Git checkout provenance")
    verify.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    verify.add_argument("--expected-commit")
    verify.add_argument("--require-origin-main", action="store_true")

    manifest = subparsers.add_parser("manifest", help="生成不可变 release manifest")
    manifest.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    manifest.add_argument("--output", type=Path, required=True)
    manifest.add_argument("--expected-commit")
    manifest.add_argument("--require-origin-main", action="store_true")
    manifest.add_argument("--version")
    manifest.add_argument("--environment")
    manifest.add_argument("--generated-at-utc")
    manifest.add_argument("--ci-run")
    manifest.add_argument("--ci-workflow")
    manifest.add_argument("--ci-conclusion", default="success")
    manifest.add_argument("--build-id")
    manifest.add_argument("--image-tag")
    manifest.add_argument("--image-digest")
    manifest.add_argument("--cloudrun-revision")
    manifest.add_argument("--deployment-time-utc")
    manifest.add_argument("--traffic-ratio", type=int)
    manifest.add_argument("--backup-id")
    manifest.add_argument("--feature-flags")
    manifest.add_argument("--migration-state")

    validate = subparsers.add_parser("validate", help="验证 release manifest")
    validate.add_argument("manifest", type=Path)
    validate.add_argument("--require-image-digest", action="store_true")
    validate.add_argument("--require-deployment", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_checkout(
                args.repo_root.resolve(),
                expected_commit=args.expected_commit,
                require_origin_main=args.require_origin_main,
            )
        elif args.command == "manifest":
            result = create_manifest(args)
        else:
            result = validate_manifest(
                args.manifest.resolve(),
                require_image_digest=args.require_image_digest,
                require_deployment=args.require_deployment,
            )
    except RuntimeError as exc:
        print(f"PROVENANCE CHECK FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
