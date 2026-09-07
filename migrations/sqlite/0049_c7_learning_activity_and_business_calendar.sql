-- 0049: C7 每日读书、优秀分享事实与中国大陆年度工作日日历
-- 本迁移只建立可追溯事实和可配置日历，不创建学分账本流水。

CREATE TABLE IF NOT EXISTS learning_business_calendar_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    calendar_key TEXT NOT NULL DEFAULT 'CHINA_MAINLAND',
    calendar_year INTEGER NOT NULL CHECK(calendar_year BETWEEN 2000 AND 2100),
    version_label TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai'
        CHECK(timezone='Asia/Shanghai'),
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK(status IN ('DRAFT', 'PUBLISHED', 'RETIRED')),
    created_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(calendar_key, calendar_year, version_label)
);
CREATE INDEX IF NOT EXISTS idx_learning_calendar_versions_lookup
    ON learning_business_calendar_versions(calendar_key, calendar_year, status, id);

CREATE TABLE IF NOT EXISTS learning_business_calendar_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    calendar_version_id INTEGER NOT NULL
        REFERENCES learning_business_calendar_versions(id) ON DELETE CASCADE,
    business_date TEXT NOT NULL,
    day_type TEXT NOT NULL
        CHECK(day_type IN ('NORMAL_WORKDAY', 'WEEKEND', 'HOLIDAY', 'ADJUSTED_WORKDAY')),
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(calendar_version_id, business_date)
);
CREATE INDEX IF NOT EXISTS idx_learning_calendar_days_lookup
    ON learning_business_calendar_days(calendar_version_id, business_date);

CREATE TABLE IF NOT EXISTS learning_credit_activity_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_type TEXT NOT NULL
        CHECK(activity_type IN ('DAILY_READING', 'EXCELLENT_SHARE')),
    member_id INTEGER NOT NULL REFERENCES members(id),
    class_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    binding_id INTEGER REFERENCES class_learning_bindings(id) ON DELETE SET NULL,
    occurred_on TEXT NOT NULL,
    participation_status TEXT NOT NULL DEFAULT 'RECORDED'
        CHECK(participation_status IN ('RECORDED', 'CONFIRMED', 'REJECTED', 'CANCELLED')),
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_type, source_id)
);
CREATE INDEX IF NOT EXISTS idx_learning_activity_facts_member_date
    ON learning_credit_activity_facts(member_id, occurred_on, activity_type, id);
CREATE INDEX IF NOT EXISTS idx_learning_activity_facts_class_date
    ON learning_credit_activity_facts(class_org_unit_id, occurred_on, activity_type, id);
CREATE INDEX IF NOT EXISTS idx_learning_activity_facts_binding
    ON learning_credit_activity_facts(binding_id, occurred_on, activity_type);

INSERT OR IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('plans:business_calendar_manage', '维护年度工作日日历', 'SENSITIVE', datetime('now'));
INSERT OR IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('plans:credit_activity_fact_manage', '维护每日读书与优秀分享事实', 'SENSITIVE', datetime('now'));
INSERT OR IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'plans:business_calendar_manage'
FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'ops_center_learning', 'ops_center_management');
INSERT OR IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'plans:credit_activity_fact_manage'
FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'ops_center_learning', 'ops_center_management');
