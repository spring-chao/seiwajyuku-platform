-- 0037: formal member profile fields used by the admin edit form.
-- These fields were previously captured only on enrollment applications.

ALTER TABLE members ADD COLUMN political_status TEXT;
ALTER TABLE members ADD COLUMN email TEXT;
ALTER TABLE members ADD COLUMN invoice_type TEXT;
ALTER TABLE members ADD COLUMN invoice_title TEXT;
ALTER TABLE members ADD COLUMN invoice_tax_id TEXT;
ALTER TABLE members ADD COLUMN goal_years TEXT;
ALTER TABLE members ADD COLUMN revenue_growth_target TEXT;
ALTER TABLE members ADD COLUMN profit_growth_target TEXT;
