from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse


def _sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("本地脚本当前只直接处理 SQLite；MySQL staging 请使用容器内 mysqldump/mysql")
    return Path(database_url[len(prefix):]).resolve()


def _guard() -> str:
    environment = os.getenv("APP_ENV", "dev").strip().lower()
    if environment == "production":
        raise RuntimeError("该脚本禁止在 production 环境运行")
    if environment not in {"dev", "test", "staging"}:
        raise RuntimeError(f"未知 APP_ENV: {environment}")
    return environment


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup(database_url: str, output_dir: Path) -> Path:
    environment = _guard()
    source = _sqlite_path(database_url)
    if not source.exists():
        raise FileNotFoundError(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = output_dir / f"{environment}-{stamp}.sqlite"
    source_db = sqlite3.connect(source)
    target_db = sqlite3.connect(target)
    try:
        source_db.backup(target_db)
    finally:
        target_db.close()
        source_db.close()
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "environment": environment,
        "source_name": source.name,
        "backup_name": target.name,
        "sha256": _sha256(target),
    }
    target.with_suffix(".json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


def restore(database_url: str, backup_file: Path, confirm_target: str) -> Path:
    environment = _guard()
    if confirm_target != environment:
        raise RuntimeError(f"确认目标不匹配：当前 {environment}，收到 {confirm_target}")
    target = _sqlite_path(database_url)
    backup_file = backup_file.resolve()
    manifest_file = backup_file.with_suffix(".json")
    if not backup_file.exists() or not manifest_file.exists():
        raise FileNotFoundError("备份文件或清单不存在")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("sha256") != _sha256(backup_file):
        raise RuntimeError("备份校验失败，拒绝恢复")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        safety_copy = target.with_suffix(target.suffix + ".pre-restore")
        shutil.copy2(target, safety_copy)
    shutil.copy2(backup_file, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="非生产 SQLite 备份/恢复")
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--output-dir", type=Path, default=Path("backups"))
    parser.add_argument("--backup-file", type=Path)
    parser.add_argument("--confirm-target")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("必须提供 --database-url 或 DATABASE_URL")
    if args.action == "backup":
        result = backup(args.database_url, args.output_dir)
        print(json.dumps({"path": str(result)}, ensure_ascii=True))
        return 0
    if not args.backup_file or not args.confirm_target:
        parser.error("restore 必须提供 --backup-file 和 --confirm-target")
    result = restore(args.database_url, args.backup_file, args.confirm_target)
    print(json.dumps({"path": str(result)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
