-- 0054 rollback is loss-averse. Do not remove employee roles that are already
-- referenced by user roles or explicit IAM2 authorization grants.
BEGIN;
CREATE TEMP TABLE iam2_staff_role_catalog_rollback_guard (n INTEGER CHECK(n=0));
INSERT INTO iam2_staff_role_catalog_rollback_guard
SELECT COUNT(*) FROM user_roles
WHERE role_key IN (
    'employee_operations_lead', 'employee_operations_management',
    'employee_member_management', 'employee_learning_management',
    'employee_development_management', 'employee_renewal_management',
    'employee_finance_management', 'employee_data_management',
    'employee_administration_management', 'read_only'
);
INSERT INTO iam2_staff_role_catalog_rollback_guard
SELECT COUNT(*) FROM employee_authorization_grants
WHERE role_key IN (
    'employee_operations_lead', 'employee_operations_management',
    'employee_member_management', 'employee_learning_management',
    'employee_development_management', 'employee_renewal_management',
    'employee_finance_management', 'employee_data_management',
    'employee_administration_management', 'read_only'
);
DROP TABLE iam2_staff_role_catalog_rollback_guard;

DELETE FROM role_permissions
WHERE role_key IN (
    'employee_operations_lead', 'employee_operations_management',
    'employee_member_management', 'employee_learning_management',
    'employee_development_management', 'employee_renewal_management',
    'employee_finance_management', 'employee_data_management',
    'employee_administration_management', 'read_only'
);
DELETE FROM roles
WHERE role_key IN (
    'employee_operations_lead', 'employee_operations_management',
    'employee_member_management', 'employee_learning_management',
    'employee_development_management', 'employee_renewal_management',
    'employee_finance_management', 'employee_data_management',
    'employee_administration_management', 'read_only'
);
DELETE FROM schema_migrations WHERE version='0054_iam2_staff_role_catalog.sql';
COMMIT;
