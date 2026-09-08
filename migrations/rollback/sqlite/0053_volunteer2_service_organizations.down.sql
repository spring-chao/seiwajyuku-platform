-- 0053 rollback is intentionally loss-averse.  It is available only before
-- a Volunteer 2.0 service organization, linked appointment, or person-level
-- employee binding has been used.  Restore a pre-migration snapshot instead
-- of discarding any of those business facts.

PRAGMA foreign_keys=OFF;
BEGIN;

CREATE TEMP TABLE volunteer2_rollback_guard (n INTEGER CHECK(n=0));
INSERT INTO volunteer2_rollback_guard
SELECT
    (SELECT COUNT(*) FROM volunteer_service_units)
    + (SELECT COUNT(*) FROM volunteer_appointment_recommendation_rules)
    + (SELECT COUNT(*) FROM volunteer_appointment_links)
    + (SELECT COUNT(*) FROM volunteer_appointments WHERE volunteer_service_unit_id IS NOT NULL)
    + (SELECT COUNT(*) FROM wechat_member_bindings WHERE person_id IS NOT NULL OR verified_user_id IS NOT NULL)
    + (SELECT COUNT(*) FROM volunteer_appointments WHERE appointment_key IN (
        'volunteer_shuku_chair', 'volunteer_shuku_learning_vice_chair',
        'volunteer_shuku_operations_vice_chair', 'volunteer_shuku_development_vice_chair',
        'volunteer_shuku_director', 'volunteer_shuku_supervisor_chair',
        'volunteer_shuku_supervisor', 'volunteer_center_chair',
        'volunteer_center_learning_vice_chair', 'volunteer_center_learning_director',
        'volunteer_center_operations_vice_chair', 'volunteer_center_operations_director',
        'volunteer_center_development_vice_chair', 'volunteer_center_development_director',
        'volunteer_center_supervisor', 'volunteer_committee_learning',
        'volunteer_committee_operations', 'volunteer_committee_development'
    ));
DROP TABLE volunteer2_rollback_guard;

ALTER TABLE volunteer_appointments RENAME TO volunteer_appointments_0053_current;
CREATE TABLE volunteer_appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person_profiles(id),
    member_id INTEGER REFERENCES members(id),
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
    updated_at TEXT NOT NULL
);
INSERT INTO volunteer_appointments
    (id, person_id, member_id, appointment_key, org_unit_id, scope_type, starts_at,
     ends_at, status, source_reference, created_at, updated_at)
SELECT id, person_id, member_id, appointment_key, org_unit_id, scope_type, starts_at,
       ends_at, status, source_reference, created_at, updated_at
FROM volunteer_appointments_0053_current;
DROP TABLE volunteer_appointments_0053_current;
CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_person
    ON volunteer_appointments(person_id, status, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_member
    ON volunteer_appointments(member_id, status);
CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_org
    ON volunteer_appointments(org_unit_id, status, starts_at, ends_at);

ALTER TABLE wechat_member_bindings RENAME TO wechat_member_bindings_0053_current;
CREATE TABLE wechat_member_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appid TEXT NOT NULL,
    openid TEXT NOT NULL,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'VERIFIED'
        CHECK(status IN ('VERIFIED', 'REVOKED')),
    active_slot INTEGER,
    binding_source TEXT NOT NULL DEFAULT 'MINIPROGRAM_SELF_SERVICE',
    verified_at TEXT,
    revoked_at TEXT,
    token_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(appid, openid),
    UNIQUE(appid, member_id, active_slot),
    CHECK((status='VERIFIED' AND active_slot=1) OR (status='REVOKED' AND active_slot IS NULL))
);
INSERT INTO wechat_member_bindings
    (id, appid, openid, member_id, status, active_slot, binding_source,
     verified_at, revoked_at, token_version, created_at, updated_at)
SELECT id, appid, openid, member_id, status, active_slot, binding_source,
       verified_at, revoked_at, token_version, created_at, updated_at
FROM wechat_member_bindings_0053_current;
DROP TABLE wechat_member_bindings_0053_current;
CREATE INDEX IF NOT EXISTS idx_wechat_member_bindings_member
    ON wechat_member_bindings(member_id, status);
CREATE INDEX IF NOT EXISTS idx_wechat_member_bindings_status
    ON wechat_member_bindings(appid, status, updated_at);

DROP TABLE volunteer_appointment_links;
DROP TABLE volunteer_appointment_recommendation_rules;
DROP TABLE volunteer_service_units;
DROP TABLE volunteer_position_profiles;
DELETE FROM volunteer_position_catalog WHERE position_key IN (
    'volunteer_shuku_chair', 'volunteer_shuku_learning_vice_chair',
    'volunteer_shuku_operations_vice_chair', 'volunteer_shuku_development_vice_chair',
    'volunteer_shuku_director', 'volunteer_shuku_supervisor_chair',
    'volunteer_shuku_supervisor', 'volunteer_center_chair',
    'volunteer_center_learning_vice_chair', 'volunteer_center_learning_director',
    'volunteer_center_operations_vice_chair', 'volunteer_center_operations_director',
    'volunteer_center_development_vice_chair', 'volunteer_center_development_director',
    'volunteer_center_supervisor', 'volunteer_committee_learning',
    'volunteer_committee_operations', 'volunteer_committee_development'
);

DELETE FROM schema_migrations WHERE version='0053_volunteer2_service_organizations.sql';
COMMIT;
PRAGMA foreign_keys=ON;
