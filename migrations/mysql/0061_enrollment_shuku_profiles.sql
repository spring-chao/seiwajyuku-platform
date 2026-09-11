-- 0061: keep shuku-specific public enrollment copy in business-owned data.
-- No rows are seeded here: payment accounts and contacts must be supplied by
-- the business before a profile becomes public.
CREATE TABLE IF NOT EXISTS enrollment_shuku_profiles (
    shuku_org_unit_id VARCHAR(64) PRIMARY KEY,
    display_name VARCHAR(255) NOT NULL,
    joining_notice TEXT NOT NULL,
    payment_instructions TEXT NULL,
    payee_name VARCHAR(255) NULL,
    bank_name VARCHAR(255) NULL,
    bank_account VARCHAR(128) NULL,
    contact_name VARCHAR(255) NULL,
    contact_phone VARCHAR(64) NULL,
    contact_address VARCHAR(1000) NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_enrollment_shuku_profile_org
        FOREIGN KEY(shuku_org_unit_id) REFERENCES org_units(id),
    INDEX idx_enrollment_shuku_profiles_active(is_active, shuku_org_unit_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
