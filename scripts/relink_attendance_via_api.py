from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

import httpx


def _name(value: object) -> str:
    return str(value or "").strip()


def _login(client: httpx.Client, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    response.raise_for_status()
    return response.json()["data"]["access_token"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按所属分中心和唯一姓名安全重关联历史签到；默认仅预览"
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--username", default="admin")
    parser.add_argument(
        "--password-env", default="SEIWAJYUKU_ADMIN_PASSWORD"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-production", action="store_true")
    args = parser.parse_args()

    if args.apply and not args.confirm_production:
        parser.error("--apply 必须同时提供 --confirm-production")
    password = os.getenv(args.password_env, "")
    if not password:
        parser.error(f"环境变量 {args.password_env} 未设置")

    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        timeout=30,
        follow_redirects=True,
    ) as client:
        token = _login(client, args.username, password)
        client.headers["Authorization"] = f"Bearer {token}"

        members_response = client.get("/api/v1/members")
        records_response = client.get("/api/v1/attendance/records")
        members_response.raise_for_status()
        records_response.raise_for_status()
        members = members_response.json()["data"]
        records = records_response.json()["data"]

        members_by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for member in members:
            members_by_key[
                (member["org_unit_id"], _name(member["name"]))
            ].append(member)

        candidates: list[tuple[dict, dict]] = []
        ambiguous = 0
        unmatched = 0
        already_linked = 0
        for record in records:
            if record.get("member_id"):
                already_linked += 1
                continue
            matches = members_by_key.get(
                (record["org_unit_id"], _name(record["name_snapshot"])),
                [],
            )
            if len(matches) == 1:
                candidates.append((record, matches[0]))
            elif len(matches) > 1:
                ambiguous += 1
            else:
                unmatched += 1

        linked = 0
        failures: list[dict[str, object]] = []
        if args.apply:
            for record, member in candidates:
                try:
                    response = client.post(
                        f"/api/v1/attendance/records/{record['id']}/adjudications",
                        json={
                            "adjudication_type": "MEMBER_RELINK",
                            "reason": "正式学员主档导入后按分中心和唯一姓名核对关联",
                            "member_id": member["id"],
                        },
                    )
                    response.raise_for_status()
                    linked += 1
                except Exception as exc:
                    failures.append(
                        {
                            "record_id": record["id"],
                            "member_id": member["id"],
                            "error": str(exc)[:500],
                        }
                    )

        print(
            json.dumps(
                {
                    "mode": "apply" if args.apply else "preview",
                    "record_count": len(records),
                    "already_linked_count": already_linked,
                    "candidate_count": len(candidates),
                    "linked_count": linked,
                    "ambiguous_count": ambiguous,
                    "unmatched_count": unmatched,
                    "failure_count": len(failures),
                    "failures": failures,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
