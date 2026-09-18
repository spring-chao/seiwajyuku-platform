"""Stamp a clean, verified source checkout before CloudBase source packaging."""
import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess

from build_provenance import verify_checkout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--build-id", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    verify_checkout(root, expected_commit=args.expected_commit, require_origin_main=True)
    destination = root / "apps/platform-api/app/build-info.json"
    relative_destination = destination.relative_to(root).as_posix()
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", relative_destination],
        cwd=root,
        check=False,
    ).returncode == 0
    if ignored:
        raise RuntimeError(f"发布印章不能被 Git 忽略：{relative_destination}")
    stamp = {
        "commit_sha": args.expected_commit,
        "version": args.expected_commit[:12],
        "build_time_utc": datetime.now(UTC).isoformat(),
        "build_id": args.build_id,
    }
    destination.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stamp))


if __name__ == "__main__":
    main()
