-- 0055 rollback removes only the additive staff-management permission.
DELETE FROM role_permissions WHERE permission_key='staff:manage';
DELETE FROM permissions WHERE permission_key='staff:manage';
DELETE FROM role_permissions WHERE permission_key='enrollment:unassigned_review';
DELETE FROM permissions WHERE permission_key='enrollment:unassigned_review';
DELETE FROM schema_migrations WHERE version='0055_staff_scope_and_business_admin.sql';
