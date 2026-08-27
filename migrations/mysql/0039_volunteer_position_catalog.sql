-- 0039: V1.2-M2 志工岗位字典与能力配置
-- 岗位事实仍保存在 volunteer_appointments；本表只提供可配置的显示名称、
-- 服务层级和 capability，不把岗位名称硬编码成业务权限。

CREATE TABLE IF NOT EXISTS volunteer_position_catalog (
    position_key VARCHAR(64) PRIMARY KEY,
    position_name VARCHAR(255) NOT NULL,
    scope_level VARCHAR(32) NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    INDEX idx_volunteer_position_catalog_active(is_active, sort_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS volunteer_position_capabilities (
    position_key VARCHAR(64) NOT NULL,
    capability_key VARCHAR(128) NOT NULL,
    PRIMARY KEY(position_key, capability_key),
    CONSTRAINT fk_volunteer_position_capability_position
        FOREIGN KEY(position_key) REFERENCES volunteer_position_catalog(position_key)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO volunteer_position_catalog
    (position_key, position_name, scope_level, is_active, sort_order, created_at, updated_at)
VALUES
    ('volunteer_class_counselor', '班主任', 'CLASS', 1, 10, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_deputy_class_teacher', '副班主任', 'CLASS', 1, 20, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_class_monitor', '班长', 'CLASS', 1, 30, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_group_counselor', '辅导员', 'GROUP', 1, 40, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_group_leader', '组长', 'GROUP', 1, 50, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_director', '理事志工', 'REGIONAL_CENTER', 1, 100, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_regional_lead', '分中心负责人志工', 'REGIONAL_CENTER', 1, 110, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_regional_service', '分中心服务志工', 'REGIONAL_CENTER', 1, 120, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_class_committee', '班委', 'CLASS', 1, 130, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_group_committee', '组委', 'GROUP', 1, 140, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_activity', '专项活动志工', 'ANY', 1, 200, UTC_TIMESTAMP(), UTC_TIMESTAMP());

INSERT IGNORE INTO volunteer_position_capabilities(position_key, capability_key)
VALUES
    ('volunteer_class_counselor', 'STUDY_MEETING_MANAGE'),
    ('volunteer_deputy_class_teacher', 'STUDY_MEETING_MANAGE'),
    ('volunteer_class_monitor', 'STUDY_MEETING_MANAGE'),
    ('volunteer_group_counselor', 'STUDY_MEETING_MANAGE'),
    ('volunteer_group_leader', 'STUDY_MEETING_MANAGE');
