-- 0037: formal member profile fields used by the admin edit form.
-- These fields were previously captured only on enrollment applications.

ALTER TABLE members ADD COLUMN political_status VARCHAR(32) NULL;
ALTER TABLE members ADD COLUMN email VARCHAR(255) NULL;
ALTER TABLE members ADD COLUMN invoice_type VARCHAR(64) NULL;
ALTER TABLE members ADD COLUMN invoice_title VARCHAR(500) NULL;
ALTER TABLE members ADD COLUMN invoice_tax_id VARCHAR(255) NULL;
ALTER TABLE members ADD COLUMN goal_years VARCHAR(64) NULL;
ALTER TABLE members ADD COLUMN revenue_growth_target VARCHAR(255) NULL;
ALTER TABLE members ADD COLUMN profit_growth_target VARCHAR(255) NULL;
