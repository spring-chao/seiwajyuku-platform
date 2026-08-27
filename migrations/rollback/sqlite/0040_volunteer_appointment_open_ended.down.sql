-- Restore the previous NOT NULL end-date contract.
-- This intentionally fails if open-ended appointments exist; history must not
-- be silently rewritten to a fabricated expiry date during rollback.

PRAGMA foreign_keys=OFF;
BEGIN;

ALTER TABLE volunteer_appointments RENAME TO volunteer_appointments_0040_open_ended;

CREATE TABLE volunteer_appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person_profiles(id),
    appointment_key TEXT NOT NULL,
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('SUBTREE', 'UNIT')),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PLANNED' CHECK (status IN (
        'PLANNED', 'ACTIVE', 'SUSPENDED', 'ENDED', 'REVOKED'
    )),
    source_reference TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (ends_at > starts_at)
);

INSERT INTO volunteer_appointments
    (id, person_id, appointment_key, org_unit_id, scope_type, starts_at, ends_at,
     status, source_reference, created_at, updated_at)
SELECT id, person_id, appointment_key, org_unit_id, scope_type, starts_at, ends_at,
       status, source_reference, created_at, updated_at
FROM volunteer_appointments_0040_open_ended;

DROP TABLE volunteer_appointments_0040_open_ended;

CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_person
    ON volunteer_appointments(person_id, status, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_org
    ON volunteer_appointments(org_unit_id, status, starts_at, ends_at);

COMMIT;
PRAGMA foreign_keys=ON;
