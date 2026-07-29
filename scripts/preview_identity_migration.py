"""Preview legacy IAM records as identity/appointment migration candidates.

This command is deliberately read-only and refuses production environments.
It never infers identity from names, phone numbers or organization labels.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "platform-api"
sys.path.insert(0, str(API_ROOT))

from app.core.settings import get_settings  # noqa: E402
from app.db import fetch_all  # noqa: E402


LEGACY_CANDIDATES = {
    "operations_admin": {
        "candidate_type": "OPERATIONS_EMPLOYMENT",
        "candidate_keys": [
            "ops_center_director",
            "ops_center_operations",
            "ops_center_learning",
            "ops_center_development",
            "ops_center_management",
            "ops_center_data",
            "ops_center_finance",
            "ops_center_administration",
        ],
        "exception": "NEEDS_CONFIRMED_EMPLOYMENT_AND_POSITION",
    },
    "regional_manager": {
        "candidate_type": "VOLUNTEER_APPOINTMENT",
        "candidate_keys": ["volunteer_director", "volunteer_regional_lead"],
        "exception": "NEEDS_CONFIRMED_APPOINTMENT_SCOPE_AND_TERM",
    },
    "class_counselor": {
        "candidate_type": "VOLUNTEER_APPOINTMENT",
        "candidate_keys": [
            "volunteer_class_counselor",
            "volunteer_class_committee",
        ],
        "exception": "NEEDS_CONFIRMED_APPOINTMENT_AND_TERM",
    },
    "group_leader": {
        "candidate_type": "VOLUNTEER_APPOINTMENT",
        "candidate_keys": [
            "volunteer_group_leader",
            "volunteer_group_committee",
        ],
        "exception": "NEEDS_CONFIRMED_APPOINTMENT_AND_TERM",
    },
    "system_admin": {
        "candidate_type": "TECHNICAL_OR_BUSINESS_ASSIGNMENT",
        "candidate_keys": ["technical_admin"],
        "exception": "NEEDS_PURPOSE_AND_BUSINESS_PERMISSION_SEPARATION",
    },
    "data_security_admin": {
        "candidate_type": "DATA_SECURITY_ASSIGNMENT",
        "candidate_keys": [],
        "exception": "KEEP_SEPARATE_FROM_TECHNICAL_ADMIN",
    },
    "read_only": {
        "candidate_type": "TEMPORARY_ACCOUNT_GRANT",
        "candidate_keys": [],
        "exception": "NEEDS_PURPOSE_AND_EXPIRY",
    },
}


def build_preview() -> dict:
    settings = get_settings()
    settings.assert_safe_startup()
    if settings.is_production:
        raise RuntimeError("本工具禁止连接生产环境；生产迁移必须另行批准")

    rows = fetch_all(
        "SELECT ur.user_id, ur.role_key, ur.valid_from, ur.valid_until "
        "FROM user_roles ur ORDER BY ur.user_id, ur.role_key"
    )
    scopes = fetch_all(
        "SELECT user_id, scope_type, org_unit_id, valid_from, valid_until "
        "FROM data_scope_grants ORDER BY user_id, id"
    )
    scopes_by_user: dict[int, list[dict]] = {}
    for scope in scopes:
        scopes_by_user.setdefault(scope["user_id"], []).append(
            {
                "scope_type": scope["scope_type"],
                "org_unit_id": scope["org_unit_id"],
                "valid_from": str(scope["valid_from"]) if scope["valid_from"] else None,
                "valid_until": str(scope["valid_until"]) if scope["valid_until"] else None,
            }
        )

    candidates = []
    exceptions = Counter()
    for row in rows:
        mapping = LEGACY_CANDIDATES.get(row["role_key"])
        exception = (
            mapping["exception"] if mapping else "UNMAPPED_LEGACY_ROLE"
        )
        exceptions[exception] += 1
        candidates.append(
            {
                "account_id": row["user_id"],
                "legacy_role": row["role_key"],
                "legacy_valid_from": (
                    str(row["valid_from"]) if row["valid_from"] else None
                ),
                "legacy_valid_until": (
                    str(row["valid_until"]) if row["valid_until"] else None
                ),
                "candidate_type": (
                    mapping["candidate_type"] if mapping else "UNKNOWN"
                ),
                "candidate_keys": (
                    mapping["candidate_keys"] if mapping else []
                ),
                "legacy_scopes": scopes_by_user.get(row["user_id"], []),
                "exception": exception,
                "automatic_write_allowed": False,
            }
        )

    return {
        "mode": "READ_ONLY_PREVIEW",
        "environment": settings.app_env,
        "automatic_identity_inference": False,
        "candidate_count": len(candidates),
        "exception_counts": dict(sorted(exceptions.items())),
        "candidates": candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="只读预览旧 IAM 到新身份/任职模型的待确认候选"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="可选 JSON 输出路径；省略时输出到标准输出",
    )
    args = parser.parse_args()
    preview = build_preview()
    rendered = json.dumps(preview, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
