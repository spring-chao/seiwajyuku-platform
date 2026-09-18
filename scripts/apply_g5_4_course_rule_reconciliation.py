#!/usr/bin/env python3
"""One-shot container entrypoint for the registered G5.4 production action.

This command accepts only the operation's typed guard values and invokes the
same service as the HTTP endpoint. It is not a SQL runner.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "platform-api"))

from app.services.course_credit_reconciliation import UNUSED_PLACEHOLDER_KEYS  # noqa: E402
from app.services.production_operations import (  # noqa: E402
    ProductionOperationError,
    apply_g5_4_course_rule_reconciliation,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-release-commit", required=True)
    parser.add_argument("--expected-production-fingerprint", required=True)
    parser.add_argument("--expected-canonical-fingerprint", required=True)
    parser.add_argument("--expected-course-rule-version-id", required=True, type=int)
    parser.add_argument("--expected-rule-count", required=True, type=int)
    parser.add_argument("--execution-reason", required=True)
    parser.add_argument("--actor-user-id", required=True, type=int)
    args = parser.parse_args()
    try:
        result = apply_g5_4_course_rule_reconciliation(
            expected_release_commit=args.expected_release_commit,
            expected_production_fingerprint=args.expected_production_fingerprint,
            expected_canonical_fingerprint=args.expected_canonical_fingerprint,
            expected_course_rule_version_id=args.expected_course_rule_version_id,
            expected_course_rule_status="DRAFT",
            expected_rule_count=args.expected_rule_count,
            expected_placeholder_keys=sorted(UNUSED_PLACEHOLDER_KEYS),
            execution_reason=args.execution_reason,
            actor_user_id=args.actor_user_id,
        )
    except ProductionOperationError as exc:
        print(json.dumps({"status": "FAILED", "code": exc.code, "message": exc.message}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
