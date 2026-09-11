-- 0061: keep shuku-specific public enrollment copy in business-owned data.
-- No rows are seeded here: payment accounts and contacts must be supplied by
-- the business before a profile becomes public.
CREATE TABLE IF NOT EXISTS enrollment_shuku_profiles (
    shuku_org_unit_id TEXT PRIMARY KEY REFERENCES org_units(id),
    display_name TEXT NOT NULL,
    joining_notice TEXT NOT NULL,
    payment_instructions TEXT,
    payee_name TEXT,
    bank_name TEXT,
    bank_account TEXT,
    contact_name TEXT,
    contact_phone TEXT,
    contact_address TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_enrollment_shuku_profiles_active
    ON enrollment_shuku_profiles(is_active, shuku_org_unit_id);
