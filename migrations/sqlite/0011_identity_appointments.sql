-- 0011: additive organization, identity, employment and volunteer appointment model.
-- Existing members, user_roles and data_scope_grants remain available for compatibility.

CREATE TABLE IF NOT EXISTS operating_institutions (
    id TEXT PRIMARY KEY,
    institution_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    institution_type TEXT NOT NULL CHECK (institution_type IN (
        'HEADQUARTERS', 'DIVISION', 'CITY_CENTER', 'OPERATIONS_CENTER'
    )),
    parent_id TEXT REFERENCES operating_institutions(id),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS institution_org_links (
    institution_id TEXT NOT NULL REFERENCES operating_institutions(id) ON DELETE CASCADE,
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    link_type TEXT NOT NULL CHECK (link_type IN ('LEGACY_REPRESENTATION', 'SERVICE_BOUNDARY')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (institution_id, org_unit_id, link_type)
);

CREATE TABLE IF NOT EXISTS person_profiles (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE', 'MERGED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_person_links (
    user_id INTEGER PRIMARY KEY REFERENCES app_users(id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES person_profiles(id),
    linked_at TEXT NOT NULL,
    linked_by INTEGER REFERENCES app_users(id),
    source_reference TEXT
);
CREATE INDEX IF NOT EXISTS idx_account_person_links_person ON account_person_links(person_id);

CREATE TABLE IF NOT EXISTS member_identities (
    member_id INTEGER PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES person_profiles(id),
    membership_started_on TEXT,
    membership_ended_on TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')),
    source_reference TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_member_identities_person ON member_identities(person_id);

CREATE TABLE IF NOT EXISTS member_membership_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES member_identities(member_id) ON DELETE CASCADE,
    membership_year INTEGER NOT NULL,
    annual_fee_amount_cents INTEGER,
    fee_status TEXT NOT NULL DEFAULT 'UNCONFIRMED' CHECK (fee_status IN (
        'UNCONFIRMED', 'PENDING', 'PAID', 'WAIVED', 'REFUNDED'
    )),
    valid_from TEXT,
    valid_until TEXT,
    source_reference TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(member_id, membership_year)
);

CREATE TABLE IF NOT EXISTS operations_employments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person_profiles(id),
    institution_id TEXT NOT NULL REFERENCES operating_institutions(id),
    employment_status TEXT NOT NULL CHECK (employment_status IN (
        'PLANNED', 'ACTIVE', 'LEAVE', 'ENDED', 'REVOKED'
    )),
    started_on TEXT,
    ended_on TEXT,
    source_reference TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operations_employments_person
    ON operations_employments(person_id, employment_status);

CREATE TABLE IF NOT EXISTS operations_position_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employment_id INTEGER NOT NULL REFERENCES operations_employments(id) ON DELETE CASCADE,
    position_key TEXT NOT NULL CHECK (position_key IN (
        'ops_center_director',
        'ops_center_operations',
        'ops_center_learning',
        'ops_center_development',
        'ops_center_management',
        'ops_center_data',
        'ops_center_finance',
        'ops_center_administration'
    )),
    valid_from TEXT,
    valid_until TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('PLANNED', 'ACTIVE', 'SUSPENDED', 'ENDED', 'REVOKED')),
    source_reference TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_position_assignments_employment
    ON operations_position_assignments(employment_id, status, valid_from, valid_until);

CREATE TABLE IF NOT EXISTS employee_service_responsibilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employment_id INTEGER NOT NULL REFERENCES operations_employments(id) ON DELETE CASCADE,
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('SUBTREE', 'UNIT')),
    valid_from TEXT,
    valid_until TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('PLANNED', 'ACTIVE', 'SUSPENDED', 'ENDED', 'REVOKED')),
    source_reference TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_employee_service_responsibilities
    ON employee_service_responsibilities(employment_id, status, valid_from, valid_until);

CREATE TABLE IF NOT EXISTS volunteer_appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person_profiles(id),
    appointment_key TEXT NOT NULL CHECK (appointment_key IN (
        'volunteer_director',
        'volunteer_regional_lead',
        'volunteer_regional_service',
        'volunteer_class_counselor',
        'volunteer_class_committee',
        'volunteer_group_leader',
        'volunteer_group_committee',
        'volunteer_activity'
    )),
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
CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_person
    ON volunteer_appointments(person_id, status, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_org
    ON volunteer_appointments(org_unit_id, status, starts_at, ends_at);

CREATE TABLE IF NOT EXISTS technical_admin_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person_profiles(id),
    assignment_purpose TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_technical_admin_assignments_person
    ON technical_admin_assignments(person_id, status, starts_at, ends_at);

INSERT OR IGNORE INTO operating_institutions
    (id, institution_code, name, institution_type, parent_id, is_active, created_at, updated_at)
VALUES
    ('institution-seiwa-hq', 'SEIWA_HQ', '盛和塾总部', 'HEADQUARTERS', NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('institution-jiangnan', 'JIANGNAN', '江南塾', 'DIVISION', 'institution-seiwa-hq', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('institution-suzhou', 'SUZHOU_CENTER', '苏州分中心', 'CITY_CENTER', 'institution-jiangnan', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('institution-suzhou-operations', 'SUZHOU_OPERATIONS_CENTER', '苏州分中心运营中心', 'OPERATIONS_CENTER', 'institution-suzhou', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT OR IGNORE INTO institution_org_links(institution_id, org_unit_id, link_type, created_at)
SELECT 'institution-suzhou', id, 'LEGACY_REPRESENTATION', CURRENT_TIMESTAMP
FROM org_units WHERE unit_code='SZ_ROOT';
