-- 0053: Volunteer 2.0 service organizations and person-level WeChat binding.
--
-- This migration is deliberately additive.  It does not infer service units
-- from class names, and it never rewrites an existing volunteer appointment.
-- Existing rows continue to use org_unit_id while newly created Volunteer 2.0
-- rows point to a formal volunteer service unit.

PRAGMA foreign_keys=OFF;
BEGIN;

CREATE TABLE IF NOT EXISTS volunteer_position_profiles (
    position_key TEXT PRIMARY KEY
        REFERENCES volunteer_position_catalog(position_key) ON DELETE CASCADE,
    system_type TEXT NOT NULL CHECK(system_type IN (
        'CLASS_TEAM', 'GOVERNANCE', 'COMMITTEE_LINE', 'ACTIVITY'
    )),
    line_type TEXT NOT NULL CHECK(line_type IN (
        'LEARNING', 'OPERATIONS', 'DEVELOPMENT', 'GENERAL', 'SUPERVISION'
    )),
    is_selectable INTEGER NOT NULL DEFAULT 1 CHECK(is_selectable IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Existing concrete positions remain usable.  Broad historical labels are
-- retained for audit/read compatibility but are not offered as new choices.
INSERT OR IGNORE INTO volunteer_position_profiles
    (position_key, system_type, line_type, is_selectable, created_at, updated_at)
VALUES
    ('volunteer_class_counselor', 'CLASS_TEAM', 'GENERAL', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_deputy_class_teacher', 'CLASS_TEAM', 'GENERAL', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_class_monitor', 'CLASS_TEAM', 'GENERAL', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_group_counselor', 'CLASS_TEAM', 'GENERAL', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_group_leader', 'CLASS_TEAM', 'GENERAL', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_director', 'GOVERNANCE', 'GENERAL', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_regional_lead', 'GOVERNANCE', 'GENERAL', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_regional_service', 'CLASS_TEAM', 'GENERAL', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_class_committee', 'CLASS_TEAM', 'GENERAL', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_group_committee', 'CLASS_TEAM', 'GENERAL', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_activity', 'ACTIVITY', 'GENERAL', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- The first formal Volunteer 2.0 catalog.  Keys carry no organization-name
-- assumptions; concrete organizations and service targets are configured in
-- volunteer_service_units.
INSERT OR IGNORE INTO volunteer_position_catalog
    (position_key, position_name, scope_level, is_active, sort_order, created_at, updated_at)
VALUES
    ('volunteer_shuku_chair', '董事长', 'ROOT', 1, 300, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_shuku_learning_vice_chair', '学习委副董事长', 'ROOT', 1, 310, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_shuku_operations_vice_chair', '运营管理委副董事长', 'ROOT', 1, 320, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_shuku_development_vice_chair', '发展建设委副董事长', 'ROOT', 1, 330, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_shuku_director', '董事', 'ROOT', 1, 340, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_shuku_supervisor_chair', '监事长', 'ROOT', 1, 350, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_shuku_supervisor', '监事', 'ROOT', 1, 360, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_chair', '分中心董事长', 'REGIONAL_CENTER', 1, 400, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_learning_vice_chair', '学习委副董事长', 'REGIONAL_CENTER', 1, 410, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_learning_director', '学习委董事', 'REGIONAL_CENTER', 1, 420, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_operations_vice_chair', '运营管理委副董事长', 'REGIONAL_CENTER', 1, 430, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_operations_director', '运营管理委董事', 'REGIONAL_CENTER', 1, 440, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_development_vice_chair', '发展建设委副董事长', 'REGIONAL_CENTER', 1, 450, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_development_director', '发展建设委董事', 'REGIONAL_CENTER', 1, 460, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_supervisor', '监事', 'REGIONAL_CENTER', 1, 470, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_committee_learning', '学习践行志工', 'CLASS', 1, 500, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_committee_operations', '运营管理志工', 'CLASS', 1, 510, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_committee_development', '发展建设志工', 'CLASS', 1, 520, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT OR IGNORE INTO volunteer_position_profiles
    (position_key, system_type, line_type, is_selectable, created_at, updated_at)
VALUES
    ('volunteer_shuku_chair', 'GOVERNANCE', 'GENERAL', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_shuku_learning_vice_chair', 'GOVERNANCE', 'LEARNING', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_shuku_operations_vice_chair', 'GOVERNANCE', 'OPERATIONS', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_shuku_development_vice_chair', 'GOVERNANCE', 'DEVELOPMENT', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_shuku_director', 'GOVERNANCE', 'GENERAL', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_shuku_supervisor_chair', 'GOVERNANCE', 'SUPERVISION', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_shuku_supervisor', 'GOVERNANCE', 'SUPERVISION', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_chair', 'GOVERNANCE', 'GENERAL', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_learning_vice_chair', 'GOVERNANCE', 'LEARNING', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_learning_director', 'GOVERNANCE', 'LEARNING', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_operations_vice_chair', 'GOVERNANCE', 'OPERATIONS', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_operations_director', 'GOVERNANCE', 'OPERATIONS', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_development_vice_chair', 'GOVERNANCE', 'DEVELOPMENT', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_development_director', 'GOVERNANCE', 'DEVELOPMENT', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_center_supervisor', 'GOVERNANCE', 'SUPERVISION', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_committee_learning', 'COMMITTEE_LINE', 'LEARNING', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_committee_operations', 'COMMITTEE_LINE', 'OPERATIONS', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_committee_development', 'COMMITTEE_LINE', 'DEVELOPMENT', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS volunteer_service_units (
    id TEXT PRIMARY KEY,
    unit_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    system_type TEXT NOT NULL CHECK(system_type IN (
        'CLASS_TEAM', 'GOVERNANCE', 'COMMITTEE_LINE', 'ACTIVITY'
    )),
    line_type TEXT NOT NULL CHECK(line_type IN (
        'LEARNING', 'OPERATIONS', 'DEVELOPMENT', 'GENERAL', 'SUPERVISION'
    )),
    parent_id TEXT REFERENCES volunteer_service_units(id),
    home_shuku_org_unit_id TEXT REFERENCES org_units(id),
    service_target_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_volunteer_service_units_parent
    ON volunteer_service_units(parent_id, is_active, sort_order);
CREATE INDEX IF NOT EXISTS idx_volunteer_service_units_target
    ON volunteer_service_units(service_target_org_unit_id, is_active);
CREATE INDEX IF NOT EXISTS idx_volunteer_service_units_home
    ON volunteer_service_units(home_shuku_org_unit_id, system_type, line_type, is_active);

ALTER TABLE volunteer_appointments
    ADD COLUMN volunteer_service_unit_id TEXT REFERENCES volunteer_service_units(id);
CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_service_unit
    ON volunteer_appointments(volunteer_service_unit_id, status);

CREATE TABLE IF NOT EXISTS volunteer_appointment_recommendation_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_service_unit_id TEXT NOT NULL REFERENCES volunteer_service_units(id) ON DELETE CASCADE,
    source_position_key TEXT NOT NULL REFERENCES volunteer_position_catalog(position_key),
    target_service_unit_id TEXT NOT NULL REFERENCES volunteer_service_units(id) ON DELETE CASCADE,
    target_position_key TEXT NOT NULL REFERENCES volunteer_position_catalog(position_key),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_service_unit_id, source_position_key, target_service_unit_id, target_position_key)
);
CREATE INDEX IF NOT EXISTS idx_volunteer_recommendation_source
    ON volunteer_appointment_recommendation_rules(source_service_unit_id, source_position_key, is_active);

CREATE TABLE IF NOT EXISTS volunteer_appointment_links (
    source_appointment_id INTEGER NOT NULL REFERENCES volunteer_appointments(id) ON DELETE CASCADE,
    target_appointment_id INTEGER NOT NULL REFERENCES volunteer_appointments(id) ON DELETE CASCADE,
    recommendation_rule_id INTEGER REFERENCES volunteer_appointment_recommendation_rules(id) ON DELETE SET NULL,
    link_type TEXT NOT NULL DEFAULT 'RECOMMENDED_PAIR' CHECK(link_type IN ('RECOMMENDED_PAIR')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(source_appointment_id, target_appointment_id),
    CHECK(source_appointment_id <> target_appointment_id)
);

-- The table name remains stable for old code and data.  It now represents a
-- WeChat credential for a natural person; member_id stays nullable so a
-- non-member employee never needs a fabricated learner record.
ALTER TABLE wechat_member_bindings RENAME TO wechat_member_bindings_0053_legacy;
CREATE TABLE wechat_member_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appid TEXT NOT NULL,
    openid TEXT NOT NULL,
    member_id INTEGER REFERENCES members(id) ON DELETE CASCADE,
    person_id TEXT REFERENCES person_profiles(id) ON DELETE SET NULL,
    verified_user_id INTEGER REFERENCES app_users(id) ON DELETE SET NULL,
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
FROM wechat_member_bindings_0053_legacy;
DROP TABLE wechat_member_bindings_0053_legacy;
CREATE INDEX IF NOT EXISTS idx_wechat_member_bindings_member
    ON wechat_member_bindings(member_id, status);
CREATE INDEX IF NOT EXISTS idx_wechat_member_bindings_person
    ON wechat_member_bindings(person_id, status);
CREATE INDEX IF NOT EXISTS idx_wechat_member_bindings_status
    ON wechat_member_bindings(appid, status, updated_at);

COMMIT;
PRAGMA foreign_keys=ON;
