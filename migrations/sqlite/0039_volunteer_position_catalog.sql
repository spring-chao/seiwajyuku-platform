-- 0039: V1.2-M2 志工岗位字典与能力配置
-- 岗位事实仍保存在 volunteer_appointments；本表只提供可配置的显示名称、
-- 服务层级和 capability，不把岗位名称硬编码成业务权限。

CREATE TABLE IF NOT EXISTS volunteer_position_catalog (
    position_key TEXT PRIMARY KEY,
    position_name TEXT NOT NULL,
    scope_level TEXT NOT NULL CHECK(scope_level IN ('REGIONAL_CENTER', 'CLASS', 'GROUP', 'ANY')),
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS volunteer_position_capabilities (
    position_key TEXT NOT NULL REFERENCES volunteer_position_catalog(position_key) ON DELETE CASCADE,
    capability_key TEXT NOT NULL,
    PRIMARY KEY(position_key, capability_key)
);

INSERT OR IGNORE INTO volunteer_position_catalog
    (position_key, position_name, scope_level, is_active, sort_order, created_at, updated_at)
VALUES
    ('volunteer_class_counselor', '班主任', 'CLASS', 1, 10, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_deputy_class_teacher', '副班主任', 'CLASS', 1, 20, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_class_monitor', '班长', 'CLASS', 1, 30, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_group_counselor', '辅导员', 'GROUP', 1, 40, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_group_leader', '组长', 'GROUP', 1, 50, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_director', '理事志工', 'REGIONAL_CENTER', 1, 100, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_regional_lead', '分中心负责人志工', 'REGIONAL_CENTER', 1, 110, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_regional_service', '分中心服务志工', 'REGIONAL_CENTER', 1, 120, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_class_committee', '班委', 'CLASS', 1, 130, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_group_committee', '组委', 'GROUP', 1, 140, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('volunteer_activity', '专项活动志工', 'ANY', 1, 200, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT OR IGNORE INTO volunteer_position_capabilities(position_key, capability_key)
VALUES
    ('volunteer_class_counselor', 'STUDY_MEETING_MANAGE'),
    ('volunteer_deputy_class_teacher', 'STUDY_MEETING_MANAGE'),
    ('volunteer_class_monitor', 'STUDY_MEETING_MANAGE'),
    ('volunteer_group_counselor', 'STUDY_MEETING_MANAGE'),
    ('volunteer_group_leader', 'STUDY_MEETING_MANAGE');

-- 0011 的 SQLite 兼容约束只列出了当时的八个岗位。M2 改为由岗位字典和
-- 应用层校验 appointment_key，保留所有历史记录和其它数据库约束。
PRAGMA foreign_keys=OFF;
ALTER TABLE volunteer_appointments RENAME TO volunteer_appointments_0039_legacy;
CREATE TABLE volunteer_appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person_profiles(id),
    appointment_key TEXT NOT NULL,
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('SUBTREE', 'UNIT')),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PLANNED' CHECK (status IN (
        'PLANNED', 'ACTIVE', 'SUSPENDED', 'ENDED', 'REVOKED'
    )),
    source_reference TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (ends_at > starts_at)
);
INSERT INTO volunteer_appointments
    (id, person_id, appointment_key, org_unit_id, scope_type, starts_at, ends_at,
     status, source_reference, created_at, updated_at)
SELECT id, person_id, appointment_key, org_unit_id, scope_type, starts_at, ends_at,
       status, source_reference, created_at, updated_at
FROM volunteer_appointments_0039_legacy;
DROP TABLE volunteer_appointments_0039_legacy;
CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_person
    ON volunteer_appointments(person_id, status, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_org
    ON volunteer_appointments(org_unit_id, status, starts_at, ends_at);
PRAGMA foreign_keys=ON;
