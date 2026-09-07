-- 0051 rollback is intentionally loss-averse. Restore a pre-0051 snapshot if
-- any explicit grants, staff contact records, or new employment values exist.
CREATE TEMPORARY TABLE iam2_staff_rollback_guard (n INT CHECK(n=0));
INSERT INTO iam2_staff_rollback_guard SELECT COUNT(*) FROM employee_authorization_grants;
INSERT INTO iam2_staff_rollback_guard SELECT COUNT(*) FROM employee_profile_details;
INSERT INTO iam2_staff_rollback_guard
SELECT COUNT(*) FROM operations_employments
WHERE department_name IS NOT NULL OR supervisor_user_id IS NOT NULL;
DROP TEMPORARY TABLE iam2_staff_rollback_guard;

DROP TABLE employee_authorization_grants;
DROP TABLE employee_profile_details;
ALTER TABLE operations_employments
    DROP FOREIGN KEY fk_operations_employment_supervisor,
    DROP INDEX idx_operations_employments_department,
    DROP COLUMN supervisor_user_id,
    DROP COLUMN department_name;
DELETE FROM schema_migrations WHERE version='0051_iam2_staff_authorization.sql';
