-- 0037 rollback removes only the newly added member profile fields.
-- Run only against a verified backup or disposable clone.
ALTER TABLE members DROP COLUMN profit_growth_target;
ALTER TABLE members DROP COLUMN revenue_growth_target;
ALTER TABLE members DROP COLUMN goal_years;
ALTER TABLE members DROP COLUMN invoice_tax_id;
ALTER TABLE members DROP COLUMN invoice_title;
ALTER TABLE members DROP COLUMN invoice_type;
ALTER TABLE members DROP COLUMN email;
ALTER TABLE members DROP COLUMN political_status;
