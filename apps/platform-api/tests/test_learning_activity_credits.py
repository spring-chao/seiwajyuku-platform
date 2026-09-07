from __future__ import annotations

from calendar import isleap
import sqlite3
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token, hash_password
from app.db import execute, fetch_one, transaction
from app.migrations import MIGRATION_ROOT
from app.main import app
from app.services.learning_activity_credits import (
    DAILY_READING,
    EXCELLENT_SHARE,
    dry_run_daily_reading,
    dry_run_excellent_shares,
    record_learning_activity_fact,
    save_business_calendar,
)
from app.services.learning_credits import LearningCreditError


@pytest.fixture(autouse=True)
def c7_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _admin_id() -> int:
    return int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])


def _published_policy_ids() -> tuple[int, int]:
    row = fetch_one(
        "SELECT generic_rule_version_id, course_credit_rule_version_id "
        "FROM learning_plan_credit_rule_mappings "
        "WHERE plan_key='standard-3y' AND plan_version_label='2026' AND status='ACTIVE'"
    )
    assert row
    return int(row["generic_rule_version_id"]), int(row["course_credit_rule_version_id"])


def _fixture() -> dict[str, int | str]:
    suffix = uuid4().hex[:12]
    center_id = f"c7-center-{suffix}"
    class_id = f"c7-class-{suffix}"
    member_code = f"C7M-{suffix}"
    now = _now()
    generic_rule_id, course_rule_id = _published_policy_ids()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, 'C7测试中心', 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
            (center_id, f"C7_CENTER_{suffix}", now, now),
        )
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, 'C7测试班', 'CLASS', ?, 1, ?, ?)",
            (class_id, f"C7_CLASS_{suffix}", center_id, now, now),
        )
        member_cursor = execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, created_at, updated_at) "
            "VALUES (?, 'C7测试学员', ?, 'ACTIVE', ?, ?)",
            (member_code, class_id, now, now),
        )
        member_id = int(member_cursor.lastrowid)
        execute(
            connection,
            "INSERT INTO member_org_relations "
            "(member_id, org_unit_id, relation_type, is_primary, valid_from, valid_until, created_at, updated_at) "
            "VALUES (?, ?, 'STUDY_CLASS', 1, '2020-01-01', NULL, ?, ?)",
            (member_id, class_id, now, now),
        )
        plan_cursor = execute(
            connection,
            "INSERT INTO learning_plan_versions "
            "(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) "
            "VALUES (?, 'C7测试计划', '2026', 36, 'PUBLISHED', ?, ?)",
            (f"C7_{suffix}", now, now),
        )
        plan_id = int(plan_cursor.lastrowid)
        binding_cursor = execute(
            connection,
            "INSERT INTO class_learning_bindings "
            "(class_org_unit_id, plan_version_id, cohort_month, started_at, status, "
            "learning_round, start_cycle_index, transition_type, credit_rule_version_id, "
            "course_credit_rule_version_id, created_at, updated_at) "
            "VALUES (?, ?, 1, '2026-01-01T00:00:00+00:00', 'ACTIVE', 1, 1, 'INITIAL', ?, ?, ?, ?)",
            (class_id, plan_id, generic_rule_id, course_rule_id, now, now),
        )
        binding_id = int(binding_cursor.lastrowid)
    return {
        "suffix": suffix,
        "center_id": center_id,
        "class_id": class_id,
        "member_id": member_id,
        "plan_id": plan_id,
        "binding_id": binding_id,
    }


def _calendar(
    fixture: dict[str, int | str],
    *,
    year: int,
    day_types: dict[str, str],
    label: str | None = None,
) -> int:
    suffix = str(fixture["suffix"])
    version_label = label or f"C7-{suffix}-{year}"
    now = _now()
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO learning_business_calendar_versions "
            "(calendar_key, calendar_year, version_label, timezone, status, created_at, updated_at) "
            "VALUES ('CHINA_MAINLAND', ?, ?, 'Asia/Shanghai', 'PUBLISHED', ?, ?)",
            (year, version_label, now, now),
        )
        version_id = int(cursor.lastrowid)
        for business_date, day_type in day_types.items():
            execute(
                connection,
                "INSERT INTO learning_business_calendar_days "
                "(calendar_version_id, business_date, day_type, note, created_at, updated_at) "
                "VALUES (?, ?, ?, 'C7 test', ?, ?)",
                (version_id, business_date, day_type, now, now),
            )
    return version_id


def _fact(
    fixture: dict[str, int | str],
    *,
    activity_type: str,
    occurred_on: str,
    source_id: str | None = None,
    status: str = "CONFIRMED",
    binding_id: int | None = None,
) -> int:
    now = _now()
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO learning_credit_activity_facts "
            "(activity_type, member_id, class_org_unit_id, binding_id, occurred_on, "
            "participation_status, source_type, source_id, metadata_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'C7_TEST', ?, '{}', ?, ?)",
            (
                activity_type,
                fixture["member_id"],
                fixture["class_id"],
                binding_id,
                occurred_on,
                status,
                source_id or uuid4().hex,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def _ledger_count() -> int:
    return int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"])


def _remove_calendar(version_id: int) -> None:
    with transaction() as connection:
        execute(
            connection,
            "DELETE FROM learning_business_calendar_versions WHERE id=?",
            (version_id,),
        )


def test_daily_reading_uses_all_configured_day_types_and_never_writes() -> None:
    fixture = _fixture()
    version_id = _calendar(
        fixture,
        year=2026,
        day_types={
            "2026-03-02": "NORMAL_WORKDAY",
            "2026-03-08": "WEEKEND",
            "2026-03-09": "HOLIDAY",
            "2026-03-14": "ADJUSTED_WORKDAY",
            "2026-03-15": "ADJUSTED_WORKDAY",
        },
    )
    try:
        for occurred_on in (
            "2026-03-02",
            "2026-03-08",
            "2026-03-09",
            "2026-03-14",
            "2026-03-15",
        ):
            _fact(
                fixture,
                activity_type=DAILY_READING,
                occurred_on=occurred_on,
                binding_id=int(fixture["binding_id"]),
            )
        before = _ledger_count()
        preview = dry_run_daily_reading(
            actor_user_id=_admin_id(), class_org_unit_id=str(fixture["class_id"])
        )
        entries = {entry["occurred_on"]: entry for entry in preview["entries"]}
        assert entries["2026-03-02"]["status"] == "READY"
        assert entries["2026-03-02"]["points"] == 1.0
        assert entries["2026-03-08"]["status"] == "NO_CREDIT"
        assert entries["2026-03-08"]["reasons"] == ["NOT_BUSINESS_WORKDAY"]
        assert entries["2026-03-09"]["status"] == "NO_CREDIT"
        assert entries["2026-03-14"]["status"] == "READY"
        assert entries["2026-03-15"]["status"] == "READY"
        assert preview["timezone"] == "Asia/Shanghai"
        assert preview["settlement_enabled"] is False
        assert preview["formal_settlement_allowed"] is False
        assert preview["write_proof"]["ledger_entries_delta"] == 0
        assert _ledger_count() == before
    finally:
        _remove_calendar(version_id)


def test_daily_reading_missing_year_calendar_is_blocked_without_weekday_fallback() -> None:
    fixture = _fixture()
    _fact(
        fixture,
        activity_type=DAILY_READING,
        occurred_on="2027-01-04",
        binding_id=int(fixture["binding_id"]),
    )
    preview = dry_run_daily_reading(
        actor_user_id=_admin_id(), class_org_unit_id=str(fixture["class_id"])
    )
    assert len(preview["entries"]) == 1
    assert preview["entries"][0]["status"] == "BLOCKED"
    assert preview["entries"][0]["reasons"] == ["BUSINESS_CALENDAR_MISSING"]
    assert preview["blocking_reasons"] == ["BUSINESS_CALENDAR_MISSING"]
    assert preview["write_proof"]["ledger_entries_delta"] == 0


def test_daily_reading_duplicate_facts_collapse_to_one_key_and_ten_previews_stay_identical() -> None:
    fixture = _fixture()
    version_id = _calendar(
        fixture, year=2026, day_types={"2026-04-06": "NORMAL_WORKDAY"}
    )
    try:
        _fact(fixture, activity_type=DAILY_READING, occurred_on="2026-04-06")
        _fact(fixture, activity_type=DAILY_READING, occurred_on="2026-04-06")
        before = _ledger_count()
        previews = [
            dry_run_daily_reading(
                actor_user_id=_admin_id(), class_org_unit_id=str(fixture["class_id"])
            )
            for _ in range(10)
        ]
        first = previews[0]
        assert len(first["entries"]) == 1
        assert first["entries"][0]["status"] == "READY"
        assert first["entries"][0]["idempotency_key"] == (
            f"DAILY_READING:{fixture['member_id']}:2026-04-06"
        )
        assert first["entries"][0]["source_fact_count"] == 2
        assert first["totals"]["proposed_points"] == 1.0
        assert all(item["entries"] == first["entries"] for item in previews)
        assert all(item["write_proof"]["ledger_entries_delta"] == 0 for item in previews)
        assert _ledger_count() == before
    finally:
        _remove_calendar(version_id)


def test_historical_fact_uses_occurrence_year_calendar_not_a_later_year_version() -> None:
    fixture = _fixture()
    first_calendar = _calendar(
        fixture,
        year=2026,
        day_types={"2026-12-31": "NORMAL_WORKDAY"},
    )
    second_calendar = _calendar(
        fixture,
        year=2027,
        day_types={"2027-01-02": "HOLIDAY"},
    )
    try:
        _fact(fixture, activity_type=DAILY_READING, occurred_on="2026-12-31")
        _fact(fixture, activity_type=DAILY_READING, occurred_on="2027-01-02")
        before = dry_run_daily_reading(
            actor_user_id=_admin_id(), class_org_unit_id=str(fixture["class_id"])
        )
        entries = {entry["occurred_on"]: entry for entry in before["entries"]}
        assert entries["2026-12-31"]["status"] == "READY"
        assert entries["2026-12-31"]["calendar"]["calendar_year"] == 2026
        assert entries["2027-01-02"]["status"] == "NO_CREDIT"
        assert entries["2027-01-02"]["calendar"]["calendar_year"] == 2027

        replacement = _calendar(
            fixture,
            year=2027,
            day_types={"2027-01-02": "ADJUSTED_WORKDAY"},
            label=f"C7-replacement-{fixture['suffix']}",
        )
        try:
            after = dry_run_daily_reading(
                actor_user_id=_admin_id(), class_org_unit_id=str(fixture["class_id"])
            )
            entries_after = {
                entry["occurred_on"]: entry for entry in after["entries"]
            }
            assert entries_after["2026-12-31"]["status"] == "READY"
            assert entries_after["2026-12-31"]["calendar"]["calendar_year"] == 2026
            assert entries_after["2027-01-02"]["status"] == "READY"
        finally:
            _remove_calendar(replacement)
    finally:
        _remove_calendar(first_calendar)
        _remove_calendar(second_calendar)


def test_excellent_share_monthly_cap_preserves_all_eight_facts_and_writes_nothing() -> None:
    fixture = _fixture()
    for day in range(1, 9):
        _fact(
            fixture,
            activity_type=EXCELLENT_SHARE,
            occurred_on=f"2026-05-{day:02d}",
        )
    before = _ledger_count()
    preview = dry_run_excellent_shares(
        actor_user_id=_admin_id(), class_org_unit_id=str(fixture["class_id"])
    )
    statuses = [entry["status"] for entry in preview["entries"]]
    assert statuses[:5] == ["READY"] * 5
    assert statuses[5:] == ["NO_CREDIT"] * 3
    assert all(
        entry["reasons"] == ["MONTHLY_CAP_REACHED"]
        for entry in preview["entries"][5:]
    )
    assert preview["totals"]["fact_count"] == 8
    assert preview["totals"]["proposed_points"] == 5.0
    assert preview["totals"]["proposed_entry_count"] == 5
    assert preview["timezone"] == "Asia/Shanghai"
    assert preview["write_proof"]["ledger_entries_delta"] == 0
    assert _ledger_count() == before
    assert int(
        fetch_one(
            "SELECT COUNT(*) AS n FROM learning_credit_activity_facts "
            "WHERE member_id=? AND activity_type='EXCELLENT_SHARE'",
            (fixture["member_id"],),
        )["n"]
    ) == 8


def test_occurrence_date_selects_the_frozen_learning_round_and_overlap_blocks() -> None:
    fixture = _fixture()
    first_calendar = _calendar(
        fixture,
        year=2026,
        day_types={"2026-06-30": "NORMAL_WORKDAY"},
    )
    second_calendar = _calendar(
        fixture,
        year=2027,
        day_types={"2027-01-04": "NORMAL_WORKDAY"},
    )
    now = _now()
    generic_rule_id, course_rule_id = _published_policy_ids()
    try:
        with transaction() as connection:
            execute(
                connection,
                "UPDATE class_learning_bindings SET status='ENDED', ended_at='2026-12-31T15:59:59+00:00' "
                "WHERE id=?",
                (fixture["binding_id"],),
            )
            plan_cursor = execute(
                connection,
                "INSERT INTO learning_plan_versions "
                "(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) "
                "VALUES (?, 'C7第二轮', '2027', 36, 'PUBLISHED', ?, ?)",
                (f"C7_SECOND_{fixture['suffix']}", now, now),
            )
            second_binding = execute(
                connection,
                "INSERT INTO class_learning_bindings "
                "(class_org_unit_id, plan_version_id, cohort_month, started_at, status, "
                "learning_round, start_cycle_index, transition_type, credit_rule_version_id, "
                "course_credit_rule_version_id, created_at, updated_at) "
                "VALUES (?, ?, 1, '2027-01-01T00:00:00+00:00', 'ACTIVE', 2, 1, 'RESTART', ?, ?, ?, ?)",
                (fixture["class_id"], plan_cursor.lastrowid, generic_rule_id, course_rule_id, now, now),
            )
            second_binding_id = int(second_binding.lastrowid)
        _fact(fixture, activity_type=DAILY_READING, occurred_on="2026-06-30")
        _fact(fixture, activity_type=DAILY_READING, occurred_on="2027-01-04")
        preview = dry_run_daily_reading(
            actor_user_id=_admin_id(), class_org_unit_id=str(fixture["class_id"])
        )
        entries = {entry["occurred_on"]: entry for entry in preview["entries"]}
        assert entries["2026-06-30"]["status"] == "READY"
        assert entries["2026-06-30"]["binding_id"] == fixture["binding_id"]
        assert entries["2026-06-30"]["learning_round"] == 1
        assert entries["2027-01-04"]["status"] == "READY"
        assert entries["2027-01-04"]["binding_id"] == second_binding_id
        assert entries["2027-01-04"]["learning_round"] == 2

        with transaction() as connection:
            plan_cursor = execute(
                connection,
                "INSERT INTO learning_plan_versions "
                "(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) "
                "VALUES (?, 'C7重叠测试计划', '2028', 36, 'PUBLISHED', ?, ?)",
                (f"C7_OVERLAP_{fixture['suffix']}", now, now),
            )
            execute(
                connection,
                "INSERT INTO class_learning_bindings "
                "(class_org_unit_id, plan_version_id, cohort_month, started_at, status, "
                "learning_round, start_cycle_index, transition_type, credit_rule_version_id, "
                "course_credit_rule_version_id, created_at, updated_at) "
                "VALUES (?, ?, 1, '2026-06-01T00:00:00+00:00', 'ACTIVE', 3, 1, 'RESTART', ?, ?, ?, ?)",
                (fixture["class_id"], plan_cursor.lastrowid, generic_rule_id, course_rule_id, now, now),
            )
        _fact(fixture, activity_type=DAILY_READING, occurred_on="2026-06-30")
        overlap = dry_run_daily_reading(
            actor_user_id=_admin_id(),
            class_org_unit_id=str(fixture["class_id"]),
            occurred_from="2026-06-30",
            occurred_to="2026-06-30",
        )
        assert overlap["entries"][0]["status"] == "BLOCKED"
        assert overlap["entries"][0]["reasons"] == ["BINDING_AMBIGUOUS"]
    finally:
        _remove_calendar(first_calendar)
        _remove_calendar(second_calendar)


def test_activity_fact_write_has_no_client_score_and_calendar_publish_is_versioned() -> None:
    fixture = _fixture()
    admin_id = _admin_id()
    with pytest.raises(LearningCreditError, match="不能携带学分"):
        record_learning_activity_fact(
            actor_user_id=admin_id,
            activity_type=DAILY_READING,
            member_id=int(fixture["member_id"]),
            class_org_unit_id=str(fixture["class_id"]),
            occurred_on="2026-07-06",
            source_type="C7_API",
            source_id=f"bad-{fixture['suffix']}",
            metadata={"points": 99},
        )

    partial = save_business_calendar(
        actor_user_id=admin_id,
        calendar_year=2099,
        version_label=f"draft-{fixture['suffix']}",
        status="DRAFT",
        days=[
            {"business_date": "2099-01-01", "day_type": "HOLIDAY"},
        ],
    )
    assert partial["status"] == "DRAFT"
    with pytest.raises(LearningCreditError, match="完整覆盖"):
        save_business_calendar(
            actor_user_id=admin_id,
            calendar_year=2099,
            version_label=f"published-{fixture['suffix']}",
            status="PUBLISHED",
            days=[
                {"business_date": "2099-01-01", "day_type": "HOLIDAY"},
            ],
        )
    with transaction() as connection:
        execute(
            connection,
            "DELETE FROM learning_business_calendar_versions WHERE id=?",
            (partial["version"]["id"],),
        )


def test_calendar_publish_requires_a_full_year_and_retires_the_previous_version() -> None:
    fixture = _fixture()
    year = 2098
    first = date(year, 1, 1)
    days = [
        {
            "business_date": (first + timedelta(days=offset)).isoformat(),
            "day_type": "NORMAL_WORKDAY",
        }
        for offset in range(366 if isleap(year) else 365)
    ]
    published = save_business_calendar(
        actor_user_id=_admin_id(),
        calendar_year=year,
        version_label=f"v1-{fixture['suffix']}",
        status="PUBLISHED",
        days=days,
    )
    replacement_days = [dict(item) for item in days]
    replacement_days[0]["day_type"] = "HOLIDAY"
    replacement = save_business_calendar(
        actor_user_id=_admin_id(),
        calendar_year=year,
        version_label=f"v2-{fixture['suffix']}",
        status="PUBLISHED",
        days=replacement_days,
    )
    try:
        assert published["status"] == "PUBLISHED"
        assert replacement["status"] == "PUBLISHED"
        old = fetch_one(
            "SELECT status FROM learning_business_calendar_versions WHERE id=?",
            (published["version"]["id"],),
        )
        assert old["status"] == "RETIRED"
    finally:
        with transaction() as connection:
            execute(
                connection,
                "DELETE FROM learning_business_calendar_versions WHERE id IN (?, ?)",
                (published["version"]["id"], replacement["version"]["id"]),
            )


def test_0049_forward_and_empty_rollback_remove_only_the_new_c7_schema() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "c7-migration.db"
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            for path in sorted((MIGRATION_ROOT / "sqlite").glob("*.sql")):
                connection.executescript(path.read_text(encoding="utf-8"))
            assert connection.execute(
                "SELECT name FROM sqlite_master WHERE name='learning_business_calendar_versions'"
            ).fetchone()
            assert connection.execute(
                "SELECT name FROM sqlite_master WHERE name='learning_credit_activity_facts'"
            ).fetchone()
            connection.executescript(
                (
                    MIGRATION_ROOT
                    / "rollback/sqlite/0049_c7_learning_activity_and_business_calendar.down.sql"
                ).read_text(encoding="utf-8")
            )
            assert connection.execute(
                "SELECT name FROM sqlite_master WHERE name='learning_business_calendar_versions'"
            ).fetchone() is None
            assert connection.execute(
                "SELECT name FROM sqlite_master WHERE name='learning_credit_activity_facts'"
            ).fetchone() is None
            assert connection.execute(
                "SELECT name FROM sqlite_master WHERE name='learning_credit_entries'"
            ).fetchone()
        finally:
            connection.close()


def test_plans_read_cannot_preview_and_api_fact_payload_cannot_supply_points() -> None:
    fixture = _fixture()
    admin_id = _admin_id()
    before = _ledger_count()
    with TestClient(app) as client:
        rejected = client.post(
            "/api/v1/learning-credits/activity-facts",
            json={
                "activity_type": "DAILY_READING",
                "member_id": fixture["member_id"],
                "class_org_unit_id": fixture["class_id"],
                "occurred_on": "2026-08-03",
                "source_type": "C7_API_TEST",
                "source_id": f"extra-{fixture['suffix']}",
                "points": 99,
            },
            headers={"Authorization": f"Bearer {_access_token(admin_id)}"},
        )
        assert rejected.status_code == 422, rejected.text
        response = client.post(
            "/api/v1/learning-credits/activity-facts",
            json={
                "activity_type": "DAILY_READING",
                "member_id": fixture["member_id"],
                "class_org_unit_id": fixture["class_id"],
                "occurred_on": "2026-08-03",
                "source_type": "C7_API_TEST",
                "source_id": f"fact-{fixture['suffix']}",
                "metadata": {},
            },
            headers={"Authorization": f"Bearer {_access_token(admin_id)}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["binding_id"] == fixture["binding_id"]
        assert _ledger_count() == before

        read_only_user = _create_read_only_user(fixture["suffix"])
        denied = client.post(
            "/api/v1/learning-credits/dry-run/daily-reading",
            params={"class_org_unit_id": fixture["class_id"]},
            headers={"Authorization": f"Bearer {_access_token(read_only_user)}"},
        )
        assert denied.status_code == 403, denied.text


def _access_token(user_id: int) -> str:
    user = fetch_one("SELECT token_version FROM app_users WHERE id=?", (user_id,))
    assert user
    return create_token(
        user_id,
        int(user["token_version"]),
        "access",
        timedelta(minutes=5),
    )


def _create_read_only_user(suffix: str) -> int:
    now = _now()
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO app_users(username, display_name, password_hash, is_active, created_at, updated_at) "
            "VALUES (?, 'C7只读测试用户', ?, 1, ?, ?)",
            (f"c7-read-{suffix}", hash_password("c7-read-only-password"), now, now),
        )
        user_id = int(cursor.lastrowid)
        execute(
            connection,
            "INSERT INTO user_roles(user_id, role_key, created_at) VALUES (?, 'read_only', ?)",
            (user_id, now),
        )
    return user_id
