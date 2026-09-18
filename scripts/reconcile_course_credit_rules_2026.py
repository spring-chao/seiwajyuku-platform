#!/usr/bin/env python3
"""Produce a fail-closed course-rule reconciliation plan.

The command consumes a sanitized, read-only production export.  ``--dry-run``
is the only executable mode in this repository.  ``--apply`` intentionally
stops without a write adapter; future production APPLY must re-read both
fingerprints inside one approved transaction.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "platform-api"))

from app.services.course_credit_reconciliation import reconcile_course_rules  # noqa: E402


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("production JSON 根节点必须是对象")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--production-json", required=True, type=Path)
    parser.add_argument("--expected-production-fingerprint")
    parser.add_argument("--expected-canonical-fingerprint")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.apply:
        if not args.expected_production_fingerprint or not args.expected_canonical_fingerprint:
            parser.error(
                "--apply 必须同时提供 --expected-production-fingerprint 和 "
                "--expected-canonical-fingerprint；本轮仍没有生产写入适配器"
            )
        message = {
            "mode": "APPLY",
            "apply_status": "NOT_EXECUTED",
            "write_performed": False,
            "blocked_reason": "APPLY_DRIVER_REQUIRED_AND_SEPARATE_PRODUCTION_APPROVAL",
            "expected_production_fingerprint": args.expected_production_fingerprint,
            "expected_canonical_fingerprint": args.expected_canonical_fingerprint,
        }
        rendered = json.dumps(message, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 2

    payload = _read_json(args.production_json)
    version = payload.get("version")
    rules = payload.get("rules")
    if not isinstance(version, dict) or not isinstance(rules, list):
        raise SystemExit("production JSON 必须包含 version 对象和 rules 数组")
    result = reconcile_course_rules(version=version, production_rules=rules)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
