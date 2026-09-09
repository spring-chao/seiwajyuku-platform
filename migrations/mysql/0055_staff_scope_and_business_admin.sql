-- 0055: separate ordinary staff management from technical IAM and align the
-- operations-center lead with the business-admin capability template.
-- Institution/org mappings remain data-owned in institution_org_links; this
-- migration intentionally does not invent Changzhou/Wuxi organization data.

INSERT IGNORE INTO permissions
    (permission_key, permission_name, sensitive_level, created_at)
VALUES
    ('staff:manage', '管理普通专职人员', 'SENSITIVE', UTC_TIMESTAMP()),
    ('enrollment:unassigned_review', '查看未分配组织的入塾申请', 'SENSITIVE', UTC_TIMESTAMP());

INSERT IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'staff:manage'
FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'employee_operations_lead');

INSERT IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'enrollment:unassigned_review'
FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'employee_operations_lead');

-- Keep the employee role key distinct, but use the complete current business
-- admin template as its source of truth. System/technical IAM permissions are
-- not part of operations_admin and are therefore not copied here.
INSERT IGNORE INTO role_permissions(role_key, permission_key)
SELECT 'employee_operations_lead', permission_key
FROM role_permissions
WHERE role_key='operations_admin';

-- The original 0011 seed could run before the imported SZ_ROOT row existed.
-- Reassert this stable-code mapping without matching on a display name.
INSERT IGNORE INTO institution_org_links(institution_id, org_unit_id, link_type, created_at)
SELECT oi.id, ou.id, 'LEGACY_REPRESENTATION', UTC_TIMESTAMP()
FROM operating_institutions oi
JOIN org_units ou ON ou.unit_code='SZ_ROOT' AND ou.is_active=1
WHERE oi.institution_code='SUZHOU_CENTER' AND oi.is_active=1;
