-- 0051 rollback is intentionally loss-averse. It is available only before
-- explicit authorization/profile data or the new employment fields are used.
BEGIN;
CREATE TEMP TABLE iam2_staff_rollback_guard (n INTEGER CHECK(n=0));
INSERT INTO iam2_staff_rollback_guard SELECT COUNT(*) FROM employee_authorization_grants;
INSERT INTO iam2_staff_rollback_guard SELECT COUNT(*) FROM employee_profile_details;
INSERT INTO iam2_staff_rollback_guard
SELECT COUNT(*) FROM operations_employments
WHERE department_name IS NOT NULL OR supervisor_user_id IS NOT NULL;
DROP TABLE iam2_staff_rollback_guard;

DROP INDEX IF EXISTS idx_employee_authorization_grants_scope;
DROP INDEX IF EXISTS idx_employee_authorization_grants_employment;
DROP TABLE employee_authorization_grants;
DROP TABLE employee_profile_details;
DROP INDEX IF EXISTS idx_operations_employments_department;
ALTER TABLE operations_employments DROP COLUMN supervisor_user_id;
ALTER TABLE operations_employments DROP COLUMN department_name;
DELETE FROM schema_migrations WHERE version='0051_iam2_staff_authorization.sql';
COMMIT;
