-- 0020: separate employee headcount from legacy company-size text and mark explicit tenure overrides.
ALTER TABLE members ADD COLUMN employee_count INTEGER;
ALTER TABLE members ADD COLUMN membership_years_overridden INTEGER NOT NULL DEFAULT 0;

