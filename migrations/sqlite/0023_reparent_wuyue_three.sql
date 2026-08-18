-- User-confirmed production correction (2026-08-14): 吴越三班 belongs to 吴江分中心.
-- The absent-source case keeps fresh development/test databases valid.

CREATE TABLE migration_guard_0023_wuyue_three (
    ok INTEGER NOT NULL CHECK (ok = 1)
);

INSERT INTO migration_guard_0023_wuyue_three(ok)
SELECT CASE
    WHEN NOT EXISTS (SELECT 1 FROM org_units WHERE id = 'org-wuyue-3') THEN 1
    WHEN EXISTS (
        SELECT 1
        FROM org_units c
        JOIN org_units p ON p.id = 'org-wujiang'
        WHERE c.id = 'org-wuyue-3'
          AND c.name = '吴越三班'
          AND c.unit_type = 'CLASS'
          AND c.is_active = 1
          AND c.parent_id IN ('org-suzhou', 'org-wujiang')
          AND p.name = '吴江分中心'
          AND p.unit_type = 'REGIONAL_CENTER'
          AND p.is_active = 1
    ) THEN 1
    ELSE 0
END;

INSERT INTO audit_logs (
    actor_user_id, action, resource_type, resource_id, org_unit_id,
    purpose, result, before_json, after_json, created_at
)
SELECT
    (SELECT id FROM app_users WHERE username = 'admin' ORDER BY id LIMIT 1),
    'org.learning_class.move',
    'org_unit',
    c.id,
    'org-wujiang',
    '业务负责人确认吴越三班属于吴江分中心；组织主数据作为分中心、班级、小组唯一口径',
    'SUCCESS',
    '{"parent_id":"org-suzhou"}',
    '{"parent_id":"org-wujiang","rollback_parent_id":"org-suzhou"}',
    CURRENT_TIMESTAMP
FROM org_units c
WHERE c.id = 'org-wuyue-3'
  AND c.parent_id = 'org-suzhou';

UPDATE org_units
SET parent_id = 'org-wujiang', updated_at = CURRENT_TIMESTAMP
WHERE id = 'org-wuyue-3'
  AND name = '吴越三班'
  AND unit_type = 'CLASS'
  AND is_active = 1
  AND parent_id = 'org-suzhou';

DROP TABLE migration_guard_0023_wuyue_three;
