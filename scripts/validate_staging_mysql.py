from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from unittest.mock import patch
from urllib.parse import urlparse

from app.core.settings import get_settings
from app.db import execute, fetch_all, transaction
from app.migrations import run_migrations
from app.services.attendance_scoring import recalculate_event_group
from app.services.attendance_sync import sync_from_signin

from backfill_member_org_relations import apply_candidates, build_candidates


def _assert_safe_target() -> None:
    settings = get_settings()
    parsed = urlparse(settings.database_url)
    if settings.app_env != "staging":
        raise RuntimeError("This validation must run with APP_ENV=staging")
    if parsed.scheme != "mysql+pymysql":
        raise RuntimeError("This validation requires a real MySQL database")
    if "staging" not in parsed.path.lower():
        raise RuntimeError("Database name must contain staging")
    if parsed.hostname not in {"127.0.0.1", "localhost", "mysql"}:
        raise RuntimeError("Remote databases are forbidden by this CI validation")


def _seed_fixture() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO app_users"
            "(id, username, display_name, password_hash, created_at, updated_at) "
            "VALUES (1, 'staging-operator', 'Staging Operator', 'not-used', ?, ?)",
            (now, now),
        )
        orgs = [
            ("r1", "R1", "Regional One", "REGIONAL_CENTER", None),
            ("c1", "C1", "Class A", "CLASS", "r1"),
            ("g1", "G1", "Group A", "GROUP", "c1"),
            ("s1", "S1", "Special A", "SPECIAL_COHORT", "r1"),
        ]
        for org_id, code, name, unit_type, parent_id in orgs:
            execute(
                connection,
                "INSERT INTO org_units"
                "(id, unit_code, name, unit_type, parent_id, is_active, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (org_id, code, name, unit_type, parent_id, now, now),
            )
        execute(
            connection,
            "INSERT INTO members"
            "(id, member_code, name, org_unit_id, status, class_name, group_name, "
            "created_at, updated_at) "
            "VALUES (1, 'M001', 'Member One', 'r1', 'ACTIVE', "
            "'Class A', 'Group A', ?, ?)",
            (now, now),
        )
        execute(
            connection,
            "INSERT INTO members"
            "(id, member_code, name, org_unit_id, status, class_name, group_name, "
            "created_at, updated_at) "
            "VALUES (2, 'M002', 'Member Two', 'r1', 'ACTIVE', "
            "'Unknown Class', 'Unknown Group', ?, ?)",
            (now, now),
        )


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _SigninClient:
    sessions = [
        ("s-morning", "MORNING", "2026-07-29T09:00:00+00:00"),
        ("s-afternoon", "AFTERNOON", "2026-07-29T13:00:00+00:00"),
        ("s-konpa", "KONPA", "2026-07-29T18:00:00+00:00"),
    ]

    def __init__(self, **_: object) -> None:
        pass

    def __enter__(self) -> "_SigninClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get(self, url: str, *, params: dict, headers: dict) -> _Response:
        if headers.get("X-API-Key") != "staging-signin-key":
            raise AssertionError("Missing staging service key")
        if url.endswith("/sessions"):
            return _Response(
                {
                    "items": [
                        {
                            "session_id": session_id,
                            "external_session_id": session_id,
                            "session_code": session_code,
                            "session_name": session_code,
                            "session_order": index + 1,
                            "scheduled_start_at": start_at,
                            "event_group": {
                                "external_group_id": "eg-001",
                                "org_unit_id": "r1",
                                "event_date": "2026-07-29",
                                "activity_type": "CLASS_MEETING",
                                "title": "Staging Three Sessions",
                            },
                        }
                        for index, (session_id, session_code, start_at) in enumerate(
                            self.sessions
                        )
                    ],
                    "next_cursor": None,
                }
            )
        session_id = str(params["session_id"])
        index = [item[0] for item in self.sessions].index(session_id)
        checked_at = [
            "2026-07-29T08:59:00+00:00",
            "2026-07-29T13:05:00+00:00",
            "2026-07-29T18:01:00+00:00",
        ][index]
        return _Response(
            {
                "items": [
                    {
                        "external_record_id": f"{session_id}-m1",
                        "member_code": "M001",
                        "name": "Member One",
                        "attendance_status": "PRESENT",
                        "checked_at": checked_at,
                        "revision": 1,
                    },
                    {
                        "external_record_id": f"{session_id}-unknown",
                        "member_code": "M999",
                        "name": "Unknown",
                        "attendance_status": "PRESENT",
                        "checked_at": checked_at,
                        "revision": 1,
                    },
                ],
                "next_cursor": None,
            }
        )


def _run_attendance_validation() -> dict:
    signin_env = {
        "SIGNIN_API_BASE_URL": "https://staging-signin.invalid/api",
        "SIGNIN_SERVICE_API_KEY": "staging-signin-key",
    }
    with (
        patch("app.services.attendance_sync.httpx.Client", _SigninClient),
        patch.dict(os.environ, signin_env),
    ):
        first_sync = sync_from_signin()
        second_sync = sync_from_signin()
    if first_sync["status"] != "SUCCESS" or first_sync["inserted"] != 6:
        raise AssertionError(first_sync)
    if second_sync["status"] != "SUCCESS" or second_sync["updated"] != 6:
        raise AssertionError(second_sync)

    event_id = fetch_all(
        "SELECT id FROM attendance_event_groups WHERE external_group_id='eg-001'"
    )[0]["id"]
    konpa_record_id = fetch_all(
        "SELECT ar.id FROM attendance_records ar "
        "JOIN attendance_sessions s ON s.id=ar.attendance_session_id "
        "WHERE s.session_code='KONPA' AND ar.member_id=1"
    )[0]["id"]
    now = datetime.now(UTC).replace(tzinfo=None)
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO attendance_adjudications"
            "(attendance_record_id, adjudication_type, reason, actor_user_id, created_at) "
            "VALUES (?, 'EARLY_LEAVE', 'staging verification', 1, ?)",
            (konpa_record_id, now),
        )
    recalculate_event_group(event_id)
    scores = fetch_all(
        "SELECT s.session_code, ar.member_id, ar.attendance_status, sr.final_points "
        "FROM attendance_score_records sr "
        "JOIN attendance_records ar ON ar.id=sr.attendance_record_id "
        "JOIN attendance_sessions s ON s.id=ar.attendance_session_id "
        "ORDER BY s.session_order, ar.member_id"
    )
    member_scores = {
        row["session_code"]: float(row["final_points"])
        for row in scores
        if row["member_id"] == 1
    }
    expected = {"MORNING": 7.0, "AFTERNOON": 6.0, "KONPA": 2.0}
    if member_scores != expected:
        raise AssertionError({"expected": expected, "actual": member_scores})
    unmatched = [
        row for row in scores if row["attendance_status"] == "UNMATCHED"
    ]
    if len(unmatched) != 3 or any(float(row["final_points"]) != 0 for row in unmatched):
        raise AssertionError(unmatched)
    return {
        "first_sync": first_sync,
        "second_sync": second_sync,
        "member_scores": member_scores,
        "member_total": sum(member_scores.values()),
        "unmatched_zero_count": len(unmatched),
    }


def main() -> int:
    _assert_safe_target()
    applied_migrations = run_migrations()
    _seed_fixture()

    candidates, issues = build_candidates()
    if len(candidates) != 4 or len(issues) != 2:
        raise AssertionError(
            {"candidate_count": len(candidates), "issue_count": len(issues)}
        )
    inserted = apply_candidates(candidates, actor_user_id=1)
    repeated = apply_candidates(candidates, actor_user_id=1)
    if inserted != 4 or repeated != 0:
        raise AssertionError({"inserted": inserted, "repeated": repeated})

    result = {
        "database": "isolated-mysql-staging",
        "migrations": applied_migrations,
        "backfill": {
            "candidate_count": len(candidates),
            "issue_count": len(issues),
            "inserted": inserted,
            "repeated_inserted": repeated,
        },
        "attendance": _run_attendance_validation(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
