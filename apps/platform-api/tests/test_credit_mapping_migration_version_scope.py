"""0064 must validate only its policy, without weakening target-rule guards."""
from pathlib import Path
import sqlite3

import pytest


ROOT = Path(__file__).resolve().parents[3]


def seed_unrelated_rules(execute):
    """An older policy can legitimately reuse keys with different values."""
    execute("INSERT INTO learning_plan_credit_rule_versions(plan_key,version_label,status,based_on_version_label,created_at,updated_at) VALUES ('LEGACY_SCOPE_TEST','0.9','DRAFT','2025','2025-01-01','2025-01-01')")
    execute("INSERT INTO learning_plan_credit_rules(rule_version_id,course_key,course_name,year_index,credit_points,status,source,aliases_json,created_at,updated_at) SELECT v.id,'Y1-SIX-DILIGENCES','旧版课程',3,999,'PENDING','BASELINE','[]','2025-01-01','2025-01-01' FROM learning_plan_credit_rule_versions v WHERE v.plan_key='LEGACY_SCOPE_TEST'")
    execute("INSERT INTO learning_credit_rule_versions(rule_set_key,version_label,status,created_at,updated_at) VALUES ('LEGACY_SCOPE_TEST','0.9','DRAFT','2025-01-01','2025-01-01')")
    execute("INSERT INTO learning_credit_rules(rule_version_id,rule_key,credit_type,settlement_model,credit_category,points,cap_points,rule_snapshot_json,status,created_at,updated_at) SELECT v.id,'DAILY_READING','DAILY_READING','MONTHLY_CAP','EXTENSION_ACTIVITY',999,999,'{}','DISABLED','2025-01-01','2025-01-01' FROM learning_credit_rule_versions v WHERE v.rule_set_key='LEGACY_SCOPE_TEST'")


def unrelated_snapshot(connection):
    return [connection.execute(sql).fetchall() for sql in (
        "SELECT r.* FROM learning_plan_credit_rules r JOIN learning_plan_credit_rule_versions v ON v.id=r.rule_version_id WHERE v.plan_key='LEGACY_SCOPE_TEST'",
        "SELECT r.* FROM learning_credit_rules r JOIN learning_credit_rule_versions v ON v.id=r.rule_version_id WHERE v.rule_set_key='LEGACY_SCOPE_TEST'",
    )]


@pytest.fixture
def database():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    for path in sorted((ROOT / "migrations/sqlite").glob("*.sql")):
        if path.name[:4] > "0063":
            break
        connection.executescript(path.read_text(encoding="utf-8"))
    seed_unrelated_rules(connection.execute)
    connection.commit()
    yield connection
    connection.close()


def apply(connection):
    connection.executescript((ROOT / "migrations/sqlite/0064_fix_credit_rule_mapping_and_binding_freeze.sql").read_text(encoding="utf-8"))


def test_other_policy_with_reused_keys_does_not_block_or_change(database):
    before = unrelated_snapshot(database)
    apply(database)
    assert unrelated_snapshot(database) == before
    assert database.execute("SELECT status FROM learning_plan_credit_rule_versions WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1'").fetchone() == ("PUBLISHED",)
    assert database.execute("SELECT COUNT(*) FROM learning_plan_credit_rule_mappings WHERE plan_key='standard-3y' AND plan_version_label='2026' AND status='ACTIVE'").fetchone() == (1,)


@pytest.mark.parametrize("damage", ["course", "generic", "missing_generic"])
def test_other_policy_never_masks_bad_or_missing_target_rules(database, damage):
    if damage == "course":
        # Seed a nonempty, incomplete target: 0064 must not silently complete it.
        database.execute("INSERT INTO learning_plan_credit_rules(rule_version_id,course_key,course_name,year_index,credit_points,status,source,aliases_json,created_at,updated_at) SELECT id,'Y1-SIX-DILIGENCES','六项精进实践',1,999,'CONFIGURED','BASELINE','[]','2026-01-01','2026-01-01' FROM learning_plan_credit_rule_versions WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1'")
    elif damage == "generic":
        database.execute("UPDATE learning_credit_rules SET points=999 WHERE rule_key='DAILY_READING' AND rule_version_id IN (SELECT id FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1')")
    else:
        database.execute("DELETE FROM learning_credit_rules WHERE rule_key='DAILY_READING' AND rule_version_id IN (SELECT id FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1')")
    database.commit()
    before = unrelated_snapshot(database)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        apply(database)
    assert unrelated_snapshot(database) == before
