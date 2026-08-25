-- 0032: L1.2-C 课程积分配置（版本化、可审计）
-- 课程积分不写回已确认的标准计划；运营修改只进入独立版本。

CREATE TABLE IF NOT EXISTS learning_plan_credit_rule_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_key TEXT NOT NULL,
    version_label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK(status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
    based_on_version_label TEXT,
    created_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(plan_key, version_label)
);

CREATE TABLE IF NOT EXISTS learning_plan_credit_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_version_id INTEGER NOT NULL REFERENCES learning_plan_credit_rule_versions(id) ON DELETE CASCADE,
    course_key TEXT NOT NULL,
    course_name TEXT NOT NULL,
    year_index INTEGER CHECK(year_index IS NULL OR year_index BETWEEN 1 AND 20),
    credit_points INTEGER NOT NULL DEFAULT 0 CHECK(credit_points BETWEEN 0 AND 999),
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(status IN ('PENDING', 'CONFIGURED')),
    source TEXT NOT NULL DEFAULT 'SYSTEM_DEFAULT'
        CHECK(source IN ('BASELINE', 'SYSTEM_DEFAULT', 'OPERATIONS')),
    aliases_json TEXT NOT NULL DEFAULT '[]',
    created_by INTEGER REFERENCES app_users(id),
    updated_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(rule_version_id, course_key)
);

CREATE INDEX IF NOT EXISTS idx_credit_rule_versions_plan
    ON learning_plan_credit_rule_versions(plan_key, status, version_label);
CREATE INDEX IF NOT EXISTS idx_credit_rules_version_year
    ON learning_plan_credit_rules(rule_version_id, year_index, course_name);

INSERT OR IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('plans:credit_rules_manage', '维护学习计划课程积分标准', 'SENSITIVE', datetime('now'));
INSERT OR IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'plans:credit_rules_manage'
FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'ops_center_learning', 'ops_center_management');
