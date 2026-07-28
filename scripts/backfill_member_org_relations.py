from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "platform-api"
sys.path.insert(0, str(API_ROOT))

from app.db import execute, fetch_all, transaction
from app.services.audit import write_audit


def _environment() -> str:
    value = os.getenv("APP_ENV", "dev").strip().lower()
    if value == "production":
        raise RuntimeError("该脚本禁止在 production 环境运行")
    if value not in {"dev", "test", "staging"}:
        raise RuntimeError(f"未知 APP_ENV: {value}")
    return value


def _org_index() -> dict[tuple[str, str], list[dict[str, Any]]]:
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in fetch_all(
        "SELECT id, name, unit_type, parent_id FROM org_units WHERE is_active=1"
    ):
        result.setdefault((row["unit_type"], row["name"].strip()), []).append(row)
    return result


def build_candidates() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    orgs = _org_index()
    candidates: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    members = fetch_all(
        "SELECT id, member_code, org_unit_id, development_org_unit_id, "
        "class_name, group_name FROM members"
    )
    for member in members:
        planned: list[tuple[str, str, bool]] = [
            ("PRIMARY_REGION", member["org_unit_id"], True)
        ]
        if member.get("development_org_unit_id"):
            planned.append(
                (
                    "DEVELOPMENT_RELATION",
                    member["development_org_unit_id"],
                    True,
                )
            )

        class_org_id: str | None = None
        class_name = (member.get("class_name") or "").strip()
        if class_name:
            class_matches = [
                *orgs.get(("CLASS", class_name), []),
                *orgs.get(("SPECIAL_COHORT", class_name), []),
            ]
            if len(class_matches) == 1:
                class_org_id = class_matches[0]["id"]
                relation_type = (
                    "SPECIAL_COHORT"
                    if class_matches[0]["unit_type"] == "SPECIAL_COHORT"
                    else "STUDY_CLASS"
                )
                planned.append((relation_type, class_org_id, True))
            else:
                issues.append(
                    {
                        "member_id": member["id"],
                        "member_code": member["member_code"],
                        "field": "class_name",
                        "value": class_name,
                        "match_count": len(class_matches),
                    }
                )

        group_name = (member.get("group_name") or "").strip()
        if group_name:
            group_matches = orgs.get(("GROUP", group_name), [])
            if class_org_id:
                group_matches = [
                    row for row in group_matches if row["parent_id"] == class_org_id
                ]
            if len(group_matches) == 1:
                planned.append(("STUDY_GROUP", group_matches[0]["id"], True))
            else:
                issues.append(
                    {
                        "member_id": member["id"],
                        "member_code": member["member_code"],
                        "field": "group_name",
                        "value": group_name,
                        "match_count": len(group_matches),
                    }
                )

        for relation_type, org_unit_id, is_primary in planned:
            candidates.append(
                {
                    "member_id": member["id"],
                    "org_unit_id": org_unit_id,
                    "relation_type": relation_type,
                    "is_primary": is_primary,
                }
            )
    return candidates, issues


def apply_candidates(candidates: list[dict[str, Any]], actor_user_id: int) -> int:
    now = datetime.now(UTC).isoformat()
    changed = 0
    with transaction() as connection:
        for item in candidates:
            existing = execute(
                connection,
                "SELECT id FROM member_org_relations WHERE member_id=? "
                "AND org_unit_id=? AND relation_type=?",
                (
                    item["member_id"],
                    item["org_unit_id"],
                    item["relation_type"],
                ),
            ).fetchone()
            if existing:
                continue
            execute(
                connection,
                "INSERT INTO member_org_relations"
                "(member_id, org_unit_id, relation_type, is_primary, source_type, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, 'LEGACY_BACKFILL', ?, ?)",
                (
                    item["member_id"],
                    item["org_unit_id"],
                    item["relation_type"],
                    1 if item["is_primary"] else 0,
                    now,
                    now,
                ),
            )
            changed += 1
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="members.org_relation.legacy_backfill",
            resource_type="member_org_relation",
            purpose="历史班级和小组文本关系回填",
            after={"candidate_count": len(candidates), "inserted_count": changed},
        )
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="非生产 member_org_relations 历史数据回填；默认仅预览"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor-user-id", type=int)
    parser.add_argument("--confirm-environment")
    args = parser.parse_args()
    environment = _environment()
    candidates, issues = build_candidates()
    inserted = 0
    if args.apply:
        if args.confirm_environment != environment:
            parser.error(
                f"--confirm-environment 必须与当前 APP_ENV={environment} 一致"
            )
        if not args.actor_user_id:
            parser.error("--apply 必须提供 --actor-user-id")
        inserted = apply_candidates(candidates, args.actor_user_id)
    print(
        json.dumps(
            {
                "environment": environment,
                "mode": "apply" if args.apply else "preview",
                "candidate_count": len(candidates),
                "inserted_count": inserted,
                "issue_count": len(issues),
                "issues": issues,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not issues else 2


if __name__ == "__main__":
    sys.exit(main())
