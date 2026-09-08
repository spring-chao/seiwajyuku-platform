from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from app.migrations import MIGRATION_ROOT


FORWARD = MIGRATION_ROOT / "sqlite" / "0052_volunteer_membership_authority.sql"
ROLLBACK = (
    MIGRATION_ROOT
    / "rollback"
    / "sqlite"
    / "0052_volunteer_membership_authority.down.sql"
)


def _connection_before_0052() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for path in sorted((MIGRATION_ROOT / "sqlite").glob("*.sql")):
        if path.name < FORWARD.name:
            connection.executescript(path.read_text(encoding="utf-8"))
    return connection


def _seed_legacy_appointments(connection: sqlite3.Connection) -> tuple[int, int]:
    now = datetime.now(UTC).isoformat()
    connection.execute(
        "INSERT INTO org_units(id, unit_code, name, unit_type, is_active, created_at, updated_at) "
        "VALUES ('migration-volunteer-center', 'MIGRATION_VOLUNTEER_CENTER', 'Migration center', "
        "'REGIONAL_CENTER', 1, ?, ?)",
        (now, now),
    )
    member_id = int(
        connection.execute(
            "INSERT INTO members(member_code, name, org_unit_id, status, created_at, updated_at) "
            "VALUES ('MIGRATION-VOLUNTEER-001', 'Linked member', "
            "'migration-volunteer-center', 'ACTIVE', ?, ?)",
            (now, now),
        ).lastrowid
    )
    connection.execute(
        "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
        "VALUES ('migration-linked-person', 'Linked person', 'ACTIVE', ?, ?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
        "VALUES ('migration-unlinked-person', 'Unlinked person', 'ACTIVE', ?, ?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO member_identities(member_id, person_id, status, source_reference, created_at, updated_at) "
        "VALUES (?, 'migration-linked-person', 'ACTIVE', 'migration-test', ?, ?)",
        (member_id, now, now),
    )
    for person_id, position_key in (
        ("migration-linked-person", "volunteer_activity"),
        ("migration-unlinked-person", "volunteer_activity"),
    ):
        connection.execute(
            "INSERT INTO volunteer_appointments(person_id, appointment_key, org_unit_id, scope_type, "
            "starts_at, ends_at, status, source_reference, created_at, updated_at) "
            "VALUES (?, ?, 'migration-volunteer-center', 'UNIT', ?, NULL, 'ACTIVE', "
            "'migration-test', ?, ?)",
            (person_id, position_key, now, now, now),
        )
    connection.commit()
    return member_id, connection.execute(
        "SELECT id FROM volunteer_appointments WHERE person_id='migration-unlinked-person'"
    ).fetchone()[0]


def test_0052_backfills_only_unambiguous_formal_member_links() -> None:
    connection = _connection_before_0052()
    try:
        member_id, unlinked_appointment_id = _seed_legacy_appointments(connection)
        connection.executescript(FORWARD.read_text(encoding="utf-8"))

        rows = connection.execute(
            "SELECT person_id, member_id FROM volunteer_appointments ORDER BY id"
        ).fetchall()
        assert rows[0]["member_id"] == member_id
        assert rows[1]["member_id"] is None
        assert rows[1]["person_id"] == "migration-unlinked-person"
        assert (
            connection.execute(
                "SELECT member_id FROM volunteer_appointments WHERE id=?",
                (unlinked_appointment_id,),
            ).fetchone()[0]
            is None
        )

        # A timestamp inversion would have violated the former business-term
        # check.  After 0052 it is allowed as non-authorizing audit data.
        connection.execute(
            "INSERT INTO volunteer_appointments(person_id, member_id, appointment_key, org_unit_id, "
            "scope_type, starts_at, ends_at, status, source_reference, created_at, updated_at) "
            "VALUES ('migration-linked-person', ?, 'volunteer_activity', "
            "'migration-volunteer-center', 'UNIT', '2030-01-01', '2020-01-01', "
            "'ACTIVE', 'migration-audit-only', '2026-01-01', '2026-01-01')",
            (member_id,),
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(ROLLBACK.read_text(encoding="utf-8"))
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(volunteer_appointments)")
        }
        assert "member_id" in columns
    finally:
        connection.close()


def test_0052_empty_rollback_restores_the_old_table_shape() -> None:
    connection = _connection_before_0052()
    try:
        connection.executescript(FORWARD.read_text(encoding="utf-8"))
        connection.executescript(ROLLBACK.read_text(encoding="utf-8"))
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(volunteer_appointments)")
        }
        assert "member_id" not in columns
    finally:
        connection.close()
