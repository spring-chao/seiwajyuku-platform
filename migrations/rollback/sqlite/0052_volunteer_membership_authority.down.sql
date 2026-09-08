-- 0052 rollback is loss-averse. Any direct member linkage must remain intact;
-- restore a pre-0052 snapshot instead of discarding that authority evidence.

PRAGMA foreign_keys=OFF;
BEGIN;

CREATE TEMP TABLE volunteer_membership_rollback_guard (n INTEGER CHECK(n=0));
INSERT INTO volunteer_membership_rollback_guard
SELECT COUNT(*) FROM volunteer_appointments WHERE member_id IS NOT NULL;
DROP TABLE volunteer_membership_rollback_guard;

ALTER TABLE volunteer_appointments RENAME TO volunteer_appointments_0052_current;

CREATE TABLE volunteer_appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person_profiles(id),
    appointment_key TEXT NOT NULL,
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('SUBTREE', 'UNIT')),
    starts_at TEXT NOT NULL,
    ends_at TEXT,
    status TEXT NOT NULL DEFAULT 'PLANNED' CHECK (status IN (
        'PLANNED', 'ACTIVE', 'SUSPENDED', 'ENDED', 'REVOKED'
    )),
    source_reference TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (ends_at IS NULL OR ends_at > starts_at)
);

INSERT INTO volunteer_appointments
    (id, person_id, appointment_key, org_unit_id, scope_type, starts_at, ends_at,
     status, source_reference, created_at, updated_at)
SELECT id, person_id, appointment_key, org_unit_id, scope_type, starts_at, ends_at,
       status, source_reference, created_at, updated_at
FROM volunteer_appointments_0052_current;

DROP TABLE volunteer_appointments_0052_current;

CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_person
    ON volunteer_appointments(person_id, status, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_org
    ON volunteer_appointments(org_unit_id, status, starts_at, ends_at);

DELETE FROM schema_migrations WHERE version='0052_volunteer_membership_authority.sql';
COMMIT;
PRAGMA foreign_keys=ON;
