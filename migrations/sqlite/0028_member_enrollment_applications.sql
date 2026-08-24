-- 0028: public enrollment applications remain separate from formal members
-- until an authorized reviewer completes every server-side enrollment gate.

CREATE TABLE IF NOT EXISTS member_enrollment_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'DISABLED')),
    active_slot INTEGER UNIQUE,
    created_by INTEGER NOT NULL REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    disabled_at TEXT,
    last_rotated_at TEXT,
    CHECK(
        (status='ACTIVE' AND active_slot=1)
        OR (status='DISABLED' AND active_slot IS NULL)
    )
);
CREATE TABLE IF NOT EXISTS member_enrollment_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_no TEXT NOT NULL UNIQUE,
    link_id INTEGER NOT NULL REFERENCES member_enrollment_links(id),
    phone_ciphertext TEXT NOT NULL,
    phone_hash TEXT NOT NULL,
    phone_last4 TEXT NOT NULL,
    phone_masked TEXT NOT NULL,
    active_phone_guard TEXT UNIQUE,
    name TEXT NOT NULL,
    gender TEXT,
    birthday TEXT,
    district TEXT,
    company_name TEXT,
    position TEXT,
    referrer TEXT,
    industry_category TEXT,
    industry TEXT,
    company_products TEXT,
    enterprise_financial_ciphertext TEXT,
    notes TEXT,
    privacy_consent_at TEXT NOT NULL,
    application_status TEXT NOT NULL DEFAULT 'SUBMITTED'
        CHECK(application_status IN ('SUBMITTED', 'APPROVED', 'REJECTED', 'ENROLLED', 'CANCELLED')),
    payment_status TEXT NOT NULL DEFAULT 'UNCONFIRMED'
        CHECK(payment_status IN ('UNCONFIRMED', 'PAID', 'WAIVED', 'SPECIAL_APPROVED')),
    duplicate_member_risk INTEGER NOT NULL DEFAULT 0,
    org_unit_id TEXT REFERENCES org_units(id),
    join_date TEXT,
    reviewed_by INTEGER REFERENCES app_users(id),
    reviewed_at TEXT,
    review_note TEXT,
    rejected_by INTEGER REFERENCES app_users(id),
    rejected_at TEXT,
    rejection_reason TEXT,
    payment_amount NUMERIC,
    payment_note TEXT,
    payment_confirmed_by INTEGER REFERENCES app_users(id),
    payment_confirmed_at TEXT,
    converted_member_id INTEGER UNIQUE REFERENCES members(id),
    converted_by INTEGER REFERENCES app_users(id),
    converted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_enrollment_app_status
    ON member_enrollment_applications(application_status, created_at);
CREATE INDEX IF NOT EXISTS idx_enrollment_app_org
    ON member_enrollment_applications(org_unit_id, application_status, created_at);
CREATE INDEX IF NOT EXISTS idx_enrollment_app_phone_hash
    ON member_enrollment_applications(phone_hash);

-- IP addresses are never stored. The guard key is a keyed digest of the
-- active link and client address, scoped to one rolling window.
CREATE TABLE IF NOT EXISTS member_enrollment_submission_guards (
    guard_key TEXT PRIMARY KEY,
    window_started_at TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
