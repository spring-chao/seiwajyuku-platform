-- 0033: V1.1.1 structured enrollment form fields.
-- Existing application rows remain readable; new public submissions use the
-- structured fields while legacy long-form columns stay nullable for history.

ALTER TABLE member_enrollment_applications
    ADD COLUMN company_tax_id VARCHAR(64) NULL,
    ADD COLUMN invoice_title VARCHAR(500) NULL,
    ADD COLUMN invoice_tax_id VARCHAR(64) NULL,
    ADD COLUMN invoice_registered_address VARCHAR(1000) NULL,
    ADD COLUMN invoice_phone VARCHAR(64) NULL,
    ADD COLUMN invoice_bank VARCHAR(255) NULL,
    ADD COLUMN invoice_account VARCHAR(128) NULL,
    ADD COLUMN industry_other VARCHAR(255) NULL,
    ADD COLUMN goal_years VARCHAR(32) NULL,
    ADD COLUMN revenue_growth_target VARCHAR(64) NULL,
    ADD COLUMN profit_growth_target VARCHAR(64) NULL,
    ADD COLUMN rules_acknowledged TINYINT(1) NOT NULL DEFAULT 0,
    ADD COLUMN rules_acknowledged_at DATETIME NULL;
