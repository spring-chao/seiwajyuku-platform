-- 0033 rollback: remove structured V1.1.1 enrollment fields.
ALTER TABLE member_enrollment_applications
    DROP COLUMN company_tax_id,
    DROP COLUMN invoice_title,
    DROP COLUMN invoice_tax_id,
    DROP COLUMN invoice_registered_address,
    DROP COLUMN invoice_phone,
    DROP COLUMN invoice_bank,
    DROP COLUMN invoice_account,
    DROP COLUMN industry_other,
    DROP COLUMN goal_years,
    DROP COLUMN revenue_growth_target,
    DROP COLUMN profit_growth_target,
    DROP COLUMN rules_acknowledged,
    DROP COLUMN rules_acknowledged_at;
