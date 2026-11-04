-- Keep all application history; a member may have multiple confirmed applications.
PRAGMA foreign_keys=OFF;
BEGIN;
CREATE TABLE enrollment_applications_0075 (
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
    converted_member_id INTEGER REFERENCES members(id),
    converted_by INTEGER REFERENCES app_users(id),
    converted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
, political_status TEXT, company_address TEXT, email TEXT, invoice_info TEXT, invoice_type TEXT, employee_count INTEGER, books_read TEXT, enrollment_reason_philosophy TEXT, enrollment_reason_change TEXT, enrollment_reason_other TEXT, learning_years_goal TEXT, learning_participation_goal TEXT, business_goal TEXT, other_goal TEXT, company_tax_id TEXT, invoice_title TEXT, invoice_tax_id TEXT, invoice_registered_address TEXT, invoice_phone TEXT, invoice_bank TEXT, invoice_account TEXT, industry_other TEXT, goal_years TEXT, revenue_growth_target TEXT, profit_growth_target TEXT, rules_acknowledged INTEGER NOT NULL DEFAULT 0, rules_acknowledged_at TEXT, social_role TEXT, target_shuku_org_unit_id TEXT REFERENCES org_units(id));
INSERT INTO enrollment_applications_0075 ("id", "application_no", "link_id", "phone_ciphertext", "phone_hash", "phone_last4", "phone_masked", "active_phone_guard", "name", "gender", "birthday", "district", "company_name", "position", "referrer", "industry_category", "industry", "company_products", "enterprise_financial_ciphertext", "notes", "privacy_consent_at", "application_status", "payment_status", "duplicate_member_risk", "org_unit_id", "join_date", "reviewed_by", "reviewed_at", "review_note", "rejected_by", "rejected_at", "rejection_reason", "payment_amount", "payment_note", "payment_confirmed_by", "payment_confirmed_at", "converted_member_id", "converted_by", "converted_at", "created_at", "updated_at", "political_status", "company_address", "email", "invoice_info", "invoice_type", "employee_count", "books_read", "enrollment_reason_philosophy", "enrollment_reason_change", "enrollment_reason_other", "learning_years_goal", "learning_participation_goal", "business_goal", "other_goal", "company_tax_id", "invoice_title", "invoice_tax_id", "invoice_registered_address", "invoice_phone", "invoice_bank", "invoice_account", "industry_other", "goal_years", "revenue_growth_target", "profit_growth_target", "rules_acknowledged", "rules_acknowledged_at", "social_role", "target_shuku_org_unit_id") SELECT "id", "application_no", "link_id", "phone_ciphertext", "phone_hash", "phone_last4", "phone_masked", "active_phone_guard", "name", "gender", "birthday", "district", "company_name", "position", "referrer", "industry_category", "industry", "company_products", "enterprise_financial_ciphertext", "notes", "privacy_consent_at", "application_status", "payment_status", "duplicate_member_risk", "org_unit_id", "join_date", "reviewed_by", "reviewed_at", "review_note", "rejected_by", "rejected_at", "rejection_reason", "payment_amount", "payment_note", "payment_confirmed_by", "payment_confirmed_at", "converted_member_id", "converted_by", "converted_at", "created_at", "updated_at", "political_status", "company_address", "email", "invoice_info", "invoice_type", "employee_count", "books_read", "enrollment_reason_philosophy", "enrollment_reason_change", "enrollment_reason_other", "learning_years_goal", "learning_participation_goal", "business_goal", "other_goal", "company_tax_id", "invoice_title", "invoice_tax_id", "invoice_registered_address", "invoice_phone", "invoice_bank", "invoice_account", "industry_other", "goal_years", "revenue_growth_target", "profit_growth_target", "rules_acknowledged", "rules_acknowledged_at", "social_role", "target_shuku_org_unit_id" FROM member_enrollment_applications;
DROP TABLE member_enrollment_applications;
ALTER TABLE enrollment_applications_0075 RENAME TO member_enrollment_applications;
CREATE INDEX idx_enrollment_app_status
    ON member_enrollment_applications(application_status, created_at);
CREATE INDEX idx_enrollment_app_org
    ON member_enrollment_applications(org_unit_id, application_status, created_at);
CREATE INDEX idx_enrollment_app_phone_hash
    ON member_enrollment_applications(phone_hash);
CREATE INDEX idx_enrollment_app_target_shuku
    ON member_enrollment_applications(target_shuku_org_unit_id, application_status, created_at);
CREATE INDEX idx_enrollment_converted_member ON member_enrollment_applications(converted_member_id);
COMMIT;
PRAGMA foreign_keys=ON;
