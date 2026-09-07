"""C7.1.2 disposable MySQL import, parity, and loss-averse rollback gate."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.db import connect, execute, fetch_all, fetch_one, transaction
from app.migrations import MIGRATION_ROOT, run_migrations
from app.services.hq_reading_import import (
    HQ_SOURCE_TYPE,
    dry_run_hq_reading_import,
    import_hq_reading_workbook,
)
from app.services.learning_activity_credits import save_business_calendar
from test_c7_1_2_hq_reading_import import (
    _confirm,
    _hq_fact_count,
    _hq_fixture,
    _row,
    _xlsx,
    _admin_id,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("mysql+pymysql://"),
    reason="C7.1.2 MySQL gate requires the disposable isolated MySQL runtime",
)


REPO_ROOT = Path(__file__).resolve().parents[3]
CALENDAR_CONFIG = REPO_ROOT / "data" / "learning-calendars" / "china-mainland-2026.json"
ROLLBACK_0050 = (
    MIGRATION_ROOT
    / "rollback"
    / "mysql"
    / "0050_c7_hq_reading_import.down.sql"
)
HQ_TABLES = (
    "hq_reading_import_batches",
    "hq_reading_source_identities",
    "hq_reading_import_observations",
)


def _table_count(table_names: tuple[str, ...] = HQ_TABLES) -> int:
    placeholders = ",".join("?" for _ in table_names)
    row = fetch_one(
        "SELECT COUNT(*) AS n FROM information_schema.tables "
        f"WHERE table_schema=DATABASE() AND table_name IN ({placeholders})",
        table_names,
    )
    return int(row["n"])


def _run_mysql_script(path: Path) -> None:
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


def _insert_hq_ledger_sample(fixture: dict) -> int:
    generic_rule = fetch_one(
        "SELECT generic_rule_version_id FROM learning_plan_credit_rule_mappings "
        "WHERE plan_key='standard-3y' AND plan_version_label='2026' "
        "AND status='ACTIVE' LIMIT 1"
    )
    assert generic_rule
    now = "2026-03-02 00:00:00"
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO learning_credit_entries "
            "(member_id, credit_category, credit_type, points, source_type, source_id, "
            "class_org_unit_id, learning_cycle_id, rule_key, rule_version, rule_version_id, "
            "rule_snapshot_json, occurred_at, posted_at, status, idempotency_key, "
            "reversal_of_entry_id, created_by, created_at, updated_at) "
            "VALUES (?, 'STANDARD_LEARNING', 'DAILY_READING', 1, ?, 'hq-rollback-ledger-sample', "
            "?, NULL, 'DAILY_READING', '2026.1', ?, '{}', ?, NULL, 'POSTED', "
            "'HQ_ROLLBACK_LEDGER_SAMPLE', NULL, ?, ?, ?)",
            (
                fixture["member_ids"]["learner"],
                HQ_SOURCE_TYPE,
                fixture["class_id"],
                int(generic_rule["generic_rule_version_id"]),
                now,
                _admin_id(),
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def test_c7_1_2_mysql_import_dry_run_parity_and_safe_rollback() -> None:
    assert os.getenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false").lower() == "false"
    assert run_migrations() == []
    migration_row = fetch_one(
        "SELECT version FROM schema_migrations "
        "WHERE version='0050_c7_hq_reading_import.sql'"
    )
    assert migration_row, "隔离MySQL必须先应用0050"
    assert _table_count() == 3

    fixture = _hq_fixture(
        specs=[
            {"key": "learner", "name": "MySQL普通学员", "group": "1组"},
            {"key": "advanced", "name": "MySQL精进学员", "group": "精进组"},
            {"key": "nogroup", "name": "MySQL空组学员", "group": None},
        ]
    )
    config = json.loads(CALENDAR_CONFIG.read_text(encoding="utf-8"))
    calendar = save_business_calendar(
        actor_user_id=_admin_id(),
        calendar_year=int(config["calendar_year"]),
        version_label=f"C7.1.2-{fixture['suffix']}",
        status="PUBLISHED",
        days=config["days"],
    )
    assert calendar["status"] == "PUBLISHED"
    assert calendar["day_count"] == 365

    content = _xlsx(
        [
            _row(name="MySQL普通学员", occurred_on="2026-03-02"),
            _row(name="MySQL精进学员", group="精进组", occurred_on="2026-03-02"),
            _row(name="MySQL空组学员", group=None, occurred_on="2026-03-02"),
        ]
    )
    imported = import_hq_reading_workbook(
        actor_user_id=_admin_id(),
        target_class_org_unit_id=str(fixture["class_id"]),
        original_filename="mysql-hq-reading.xlsx",
        content=content,
    )
    confirmation = _confirm(
        imported,
        {
            ("MySQL普通学员", "1组", None): fixture["member_ids"]["learner"],
            ("MySQL精进学员", "精进组", None): fixture["member_ids"]["advanced"],
            ("MySQL空组学员", None, None): fixture["member_ids"]["nogroup"],
        },
    )
    fact_count = _hq_fact_count(fixture)
    assert fact_count == 2, json.dumps(
        {
            "fact_count": fact_count,
            "confirmation": confirmation,
            "observations": fetch_all(
                "SELECT source_name, source_group_name, recording_status, "
                "match_status, personal_credit_eligible, eligibility_reason "
                "FROM hq_reading_import_observations WHERE target_class_org_unit_id=? "
                "ORDER BY id",
                (fixture["class_id"],),
            ),
            "facts": fetch_all(
                "SELECT member_id, class_org_unit_id, binding_id, occurred_on, source_id "
                "FROM learning_credit_activity_facts WHERE source_type=? AND class_org_unit_id=? "
                "ORDER BY id",
                (HQ_SOURCE_TYPE, fixture["class_id"]),
            ),
        },
        ensure_ascii=False,
        default=str,
    )
    ledger_before = int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"])
    preview = dry_run_hq_reading_import(
        actor_user_id=_admin_id(), batch_id=int(imported["batch"]["batch_id"])
    )
    details = {item["source_name"]: item for item in preview["details"]}
    assert details["MySQL普通学员"]["projected_status"] == "READY"
    assert details["MySQL精进学员"]["projected_status"] == "READY"
    assert details["MySQL空组学员"]["projected_status"] == "BLOCKED"
    assert preview["summary"]["proposed_points"] == 2.0
    assert preview["write_proof"]["ledger_entries_delta"] == 0
    assert int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]) == ledger_before

    # A populated 0050 observation or HQ source fact prevents a destructive
    # downgrade.  The guard is checked before any table is dropped.
    with pytest.raises(Exception):
        _run_mysql_script(ROLLBACK_0050)
    assert _table_count() == 3

    with transaction() as connection:
        execute(
            connection,
            "DELETE FROM learning_credit_activity_facts WHERE source_type=?",
            (HQ_SOURCE_TYPE,),
        )
        execute(
            connection,
            "DELETE FROM hq_reading_import_observations WHERE target_class_org_unit_id=?",
            (fixture["class_id"],),
        )
        execute(
            connection,
            "DELETE FROM hq_reading_source_identities WHERE target_class_org_unit_id=?",
            (fixture["class_id"],),
        )
        execute(
            connection,
            "DELETE FROM hq_reading_import_batches WHERE target_class_org_unit_id=?",
            (fixture["class_id"],),
        )

    ledger_id = _insert_hq_ledger_sample(fixture)
    with pytest.raises(Exception):
        _run_mysql_script(ROLLBACK_0050)
    assert _table_count() == 3
    with transaction() as connection:
        execute(connection, "DELETE FROM learning_credit_entries WHERE id=?", (ledger_id,))

    # Empty rollback is the only permitted in-place downgrade.  Production
    # rollback remains a pre-migration snapshot restore, but this proves the
    # checked disposable path is executable on MySQL.
    _run_mysql_script(ROLLBACK_0050)
    assert _table_count() == 0
    assert fetch_one(
        "SELECT version FROM schema_migrations "
        "WHERE version='0050_c7_hq_reading_import.sql'"
    ) is None
