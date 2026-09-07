-- 0051: IAM 2.0 explicit employee role-and-scope authorization.
--
-- This is deliberately additive. Existing position-derived authorization stays
-- readable until an employment receives an explicit authorization grant.

ALTER TABLE operations_employments ADD COLUMN department_name TEXT;
ALTER TABLE operations_employments ADD COLUMN supervisor_user_id INTEGER REFERENCES app_users(id);
CREATE INDEX IF NOT EXISTS idx_operations_employments_department
    ON operations_employments(department_name, employment_status);

CREATE TABLE IF NOT EXISTS employee_profile_details (
    person_id TEXT PRIMARY KEY REFERENCES person_profiles(id) ON DELETE CASCADE,
    work_phone_ciphertext TEXT,
    work_phone_hash TEXT UNIQUE,
    work_phone_last4 TEXT,
    work_phone_masked TEXT,
    gender TEXT CHECK(gender IN ('MALE', 'FEMALE', 'UNSPECIFIED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employee_authorization_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employment_id INTEGER NOT NULL REFERENCES operations_employments(id) ON DELETE CASCADE,
    role_key TEXT NOT NULL REFERENCES roles(role_key),
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    scope_type TEXT NOT NULL CHECK(scope_type IN ('UNIT', 'SUBTREE')),
    valid_from TEXT,
    valid_until TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK(status IN ('PLANNED', 'ACTIVE', 'SUSPENDED', 'ENDED', 'REVOKED')),
    source_reference TEXT,
    created_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from)
);
CREATE INDEX IF NOT EXISTS idx_employee_authorization_grants_employment
    ON employee_authorization_grants(employment_id, status, valid_from, valid_until);
CREATE INDEX IF NOT EXISTS idx_employee_authorization_grants_scope
    ON employee_authorization_grants(org_unit_id, role_key, status, valid_from, valid_until);
