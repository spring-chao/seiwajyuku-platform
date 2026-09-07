"""C7.1.1 disposable MySQL migration, parity, and safe-rollback gate."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.db import connect, execute, fetch_one, transaction
from app.migrations import MIGRATION_ROOT, run_migrations
from app.services.learning_activity_credits import (
    DAILY_READING,
    EXCELLENT_SHARE,
    dry_run_daily_reading,
    dry_run_excellent_shares,
    record_learning_activity_fact,
    save_business_calendar,
)
from c7_1_1_parity import (
    EXPECTED_DAILY_READING_SIGNATURE,
    EXPECTED_EXCELLENT_SHARE_SIGNATURE,
    preview_parity_signature,
)
from test_learning_activity_credits import _admin_id, _fixture, _ledger_count


pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("mysql+pymysql://"),
    reason="C7.1.1 MySQL gate requires the disposable isolated MySQL runtime",
)


REPO_ROOT = Path(__file__).resolve().parents[3]
CALENDAR_CONFIG = REPO_ROOT / "data" / "learning-calendars" / "china-mainland-2026.json"
C7_TABLES = (
    "learning_business_calendar_versions",
    "learning_business_calendar_days",
    "learning_credit_activity_facts",
)
ROLLBACK_0049 = (
    MIGRATION_ROOT
    / "rollback"
    / "mysql"
    / "0049_c7_learning_activity_and_business_calendar.down.sql"
)


@pytest.fixture(autouse=True)
def c711_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")


def _table_count(table_names: tuple[str, ...] = C7_TABLES) -> int:
    placeholders = ",".join("?" for _ in table_names)
    row = fetch_one(
        "SELECT COUNT(*) AS n FROM information_schema.tables "
        f"WHERE table_schema=DATABASE() AND table_name IN ({placeholders})",
        table_names,
    )
    return int(row["n"])


def _fact_count() -> int:
    return int(
        fetch_one("SELECT COUNT(*) AS n FROM learning_credit_activity_facts")["n"]
    )


def _run_mysql_script(path: Path) -> None:
    """Run one disposable MySQL script with rollback on the first failure."""

    connection = connect()
    try:
        for raw_statement in path.read_text(encoding="utf-8").split(";"):
            statement = raw_statement.strip()
            if statement:
                execute(connection, statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _record_sample_fact(
    fixture: dict[str, int | str],
    *,
    activity_type: str,
    occurred_on: str,
    source_id: str,
) -> None:
    record_learning_activity_fact(
        actor_user_id=_admin_id(),
        activity_type=activity_type,
        member_id=int(fixture["member_id"]),
        class_org_unit_id=str(fixture["class_id"]),
        occurred_on=occurred_on,
        source_type="C7_1_1_MYSQL_SAMPLE",
        source_id=source_id,
        participation_status="CONFIRMED",
        title="C7.1.1隔离样本（非生产数据）",
        metadata={"sample_scope": "isolated_mysql"},
        binding_id=int(fixture["binding_id"]),
    )


def _insert_sample_ledger_entry(fixture: dict[str, int | str]) -> int:
    generic_rule = fetch_one(
        "SELECT generic_rule_version_id "
        "FROM learning_plan_credit_rule_mappings "
        "WHERE plan_key='standard-3y' AND plan_version_label='2026' "
        "AND status='ACTIVE' LIMIT 1"
    )
    assert generic_rule
    now = "2026-01-05 00:00:00"
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO learning_credit_entries "
            "(member_id, credit_category, credit_type, points, source_type, source_id, "
            "class_org_unit_id, learning_cycle_id, rule_key, rule_version, rule_version_id, "
            "rule_snapshot_json, occurred_at, posted_at, status, idempotency_key, "
            "reversal_of_entry_id, created_by, created_at, updated_at) "
            "VALUES (?, 'STANDARD_LEARNING', 'DAILY_READING', 1, "
            "'LEARNING_ACTIVITY_DAILY_READING', 'c7-1-1-ledger-sample', ?, NULL, "
            "'DAILY_READING', '2026.1', ?, '{}', ?, NULL, 'POSTED', "
            "'C7_1_1_MYSQL_LEDGER_SAMPLE', NULL, ?, ?, ?)",
            (
                int(fixture["member_id"]),
                str(fixture["class_id"]),
                int(generic_rule["generic_rule_version_id"]),
                now,
                _admin_id(),
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def test_c7_1_1_mysql_forward_dry_run_parity_and_loss_averse_rollback() -> None:
    """Exercise 0049 and its guards entirely inside disposable MySQL."""

    assert os.getenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false").lower() == "false"
    assert run_migrations() == []
    migration_row = fetch_one(
        "SELECT version FROM schema_migrations "
        "WHERE version='0049_c7_learning_activity_and_business_calendar.sql'"
    )
    assert migration_row, "conftest must apply 0049 before this gate runs"
    assert _table_count() == 3

    fixture = _fixture()
    config = json.loads(CALENDAR_CONFIG.read_text(encoding="utf-8"))
    calendar = save_business_calendar(
        actor_user_id=_admin_id(),
        calendar_year=int(config["calendar_year"]),
        version_label=str(config["version_label"]),
        status="PUBLISHED",
        days=config["days"],
    )
    assert calendar["status"] == "PUBLISHED"
    assert calendar["day_count"] == 365

    for occurred_on in (
        "2026-01-05",
        "2026-01-04",
        "2026-02-14",
        "2026-01-01",
        "2026-01-11",
        "2027-01-01",
    ):
        _record_sample_fact(
            fixture,
            activity_type=DAILY_READING,
            occurred_on=occurred_on,
            source_id=f"daily-{occurred_on}",
        )
    for day in range(1, 7):
        _record_sample_fact(
            fixture,
            activity_type=EXCELLENT_SHARE,
            occurred_on=f"2026-05-{day:02d}",
            source_id=f"excellent-2026-05-{day:02d}",
        )

    ledger_before = _ledger_count()
    facts_before = _fact_count()
    daily = dry_run_daily_reading(
        actor_user_id=_admin_id(), class_org_unit_id=str(fixture["class_id"])
    )
    excellent = dry_run_excellent_shares(
        actor_user_id=_admin_id(), class_org_unit_id=str(fixture["class_id"])
    )
    assert preview_parity_signature(daily) == EXPECTED_DAILY_READING_SIGNATURE
    assert (
        preview_parity_signature(excellent) == EXPECTED_EXCELLENT_SHARE_SIGNATURE
    )
    assert daily["totals"]["proposed_points"] == 3.0
    assert excellent["totals"]["proposed_points"] == 5.0
    assert daily["settlement_enabled"] is False
    assert excellent["settlement_enabled"] is False
    assert daily["formal_settlement_allowed"] is False
    assert excellent["formal_settlement_allowed"] is False
    assert daily["write_proof"]["ledger_entries_delta"] == 0
    assert excellent["write_proof"]["ledger_entries_delta"] == 0
    assert _ledger_count() == ledger_before
    assert _fact_count() == facts_before
    assert _table_count(("learning_credit_proposals", "learning_credit_staging")) == 0

    # Existing C7 facts must prevent a destructive downgrade and leave all
    # three C7 tables in place after the rejected script.
    with pytest.raises(Exception):
        _run_mysql_script(ROLLBACK_0049)
    assert _table_count() == 3

    with transaction() as connection:
        execute(
            connection,
            "DELETE FROM learning_credit_activity_facts "
            "WHERE source_type='C7_1_1_MYSQL_SAMPLE'",
        )
    ledger_id = _insert_sample_ledger_entry(fixture)

    # A ledger row is independently protected, even after all C7 source facts
    # have been removed.
    with pytest.raises(Exception):
        _run_mysql_script(ROLLBACK_0049)
    assert _table_count() == 3
    assert _ledger_count() == ledger_before + 1

    with transaction() as connection:
        execute(connection, "DELETE FROM learning_credit_entries WHERE id=?", (ledger_id,))

    # Empty rollback is allowed and removes only 0049's schema/permissions;
    # the pre-existing ledger table must survive.
    _run_mysql_script(ROLLBACK_0049)
    assert _table_count() == 0
    assert _ledger_count() == ledger_before
    assert fetch_one(
        "SELECT version FROM schema_migrations "
        "WHERE version='0049_c7_learning_activity_and_business_calendar.sql'"
    ) is None
