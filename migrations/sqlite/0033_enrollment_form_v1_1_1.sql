-- 0033: V1.1.1 structured enrollment form fields.
-- Existing application rows remain readable and new public submissions use the
-- structured fields while legacy long-form columns stay nullable for history.

ALTER TABLE member_enrollment_applications ADD COLUMN company_tax_id TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN invoice_title TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN invoice_tax_id TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN invoice_registered_address TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN invoice_phone TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN invoice_bank TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN invoice_account TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN industry_other TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN goal_years TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN revenue_growth_target TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN profit_growth_target TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN rules_acknowledged INTEGER NOT NULL DEFAULT 0;
ALTER TABLE member_enrollment_applications ADD COLUMN rules_acknowledged_at TEXT;
