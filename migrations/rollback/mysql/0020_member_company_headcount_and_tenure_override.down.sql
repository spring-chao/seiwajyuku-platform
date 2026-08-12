-- 0020 rollback removes only the newly separated headcount and override marker.
-- Run only against a verified backup or disposable clone.
ALTER TABLE members DROP COLUMN membership_years_overridden;
ALTER TABLE members DROP COLUMN employee_count;

