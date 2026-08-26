-- 0033 rollback: remove structured V1.1.1 enrollment fields.
ALTER TABLE member_enrollment_applications DROP COLUMN company_tax_id;
ALTER TABLE member_enrollment_applications DROP COLUMN invoice_title;
ALTER TABLE member_enrollment_applications DROP COLUMN invoice_tax_id;
ALTER TABLE member_enrollment_applications DROP COLUMN invoice_registered_address;
ALTER TABLE member_enrollment_applications DROP COLUMN invoice_phone;
ALTER TABLE member_enrollment_applications DROP COLUMN invoice_bank;
ALTER TABLE member_enrollment_applications DROP COLUMN invoice_account;
ALTER TABLE member_enrollment_applications DROP COLUMN industry_other;
ALTER TABLE member_enrollment_applications DROP COLUMN goal_years;
ALTER TABLE member_enrollment_applications DROP COLUMN revenue_growth_target;
ALTER TABLE member_enrollment_applications DROP COLUMN profit_growth_target;
ALTER TABLE member_enrollment_applications DROP COLUMN rules_acknowledged;
ALTER TABLE member_enrollment_applications DROP COLUMN rules_acknowledged_at;
