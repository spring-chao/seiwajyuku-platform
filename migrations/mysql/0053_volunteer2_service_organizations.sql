-- 0053: Volunteer 2.0 service organizations and person-level WeChat binding.
-- No existing volunteer appointment is inferred or migrated by this script.

CREATE TABLE IF NOT EXISTS volunteer_position_profiles (
    position_key VARCHAR(64) PRIMARY KEY,
    system_type VARCHAR(32) NOT NULL,
    line_type VARCHAR(32) NOT NULL,
    is_selectable TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_volunteer_position_profile_system CHECK(system_type IN ('CLASS_TEAM', 'GOVERNANCE', 'COMMITTEE_LINE', 'ACTIVITY')),
    CONSTRAINT chk_volunteer_position_profile_line CHECK(line_type IN ('LEARNING', 'OPERATIONS', 'DEVELOPMENT', 'GENERAL', 'SUPERVISION')),
    CONSTRAINT fk_volunteer_position_profile_catalog FOREIGN KEY(position_key)
        REFERENCES volunteer_position_catalog(position_key) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO volunteer_position_profiles
    (position_key, system_type, line_type, is_selectable, created_at, updated_at)
VALUES
    ('volunteer_class_counselor', 'CLASS_TEAM', 'GENERAL', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_deputy_class_teacher', 'CLASS_TEAM', 'GENERAL', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_class_monitor', 'CLASS_TEAM', 'GENERAL', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_group_counselor', 'CLASS_TEAM', 'GENERAL', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_group_leader', 'CLASS_TEAM', 'GENERAL', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_director', 'GOVERNANCE', 'GENERAL', 0, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_regional_lead', 'GOVERNANCE', 'GENERAL', 0, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_regional_service', 'CLASS_TEAM', 'GENERAL', 0, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_class_committee', 'CLASS_TEAM', 'GENERAL', 0, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_group_committee', 'CLASS_TEAM', 'GENERAL', 0, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_activity', 'ACTIVITY', 'GENERAL', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP());

INSERT IGNORE INTO volunteer_position_catalog
    (position_key, position_name, scope_level, is_active, sort_order, created_at, updated_at)
VALUES
    ('volunteer_shuku_chair', '董事长', 'ROOT', 1, 300, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_shuku_learning_vice_chair', '学习委副董事长', 'ROOT', 1, 310, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_shuku_operations_vice_chair', '运营管理委副董事长', 'ROOT', 1, 320, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_shuku_development_vice_chair', '发展建设委副董事长', 'ROOT', 1, 330, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_shuku_director', '董事', 'ROOT', 1, 340, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_shuku_supervisor_chair', '监事长', 'ROOT', 1, 350, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_shuku_supervisor', '监事', 'ROOT', 1, 360, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_chair', '分中心董事长', 'REGIONAL_CENTER', 1, 400, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_learning_vice_chair', '学习委副董事长', 'REGIONAL_CENTER', 1, 410, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_learning_director', '学习委董事', 'REGIONAL_CENTER', 1, 420, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_operations_vice_chair', '运营管理委副董事长', 'REGIONAL_CENTER', 1, 430, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_operations_director', '运营管理委董事', 'REGIONAL_CENTER', 1, 440, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_development_vice_chair', '发展建设委副董事长', 'REGIONAL_CENTER', 1, 450, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_development_director', '发展建设委董事', 'REGIONAL_CENTER', 1, 460, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_supervisor', '监事', 'REGIONAL_CENTER', 1, 470, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_committee_learning', '学习践行志工', 'CLASS', 1, 500, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_committee_operations', '运营管理志工', 'CLASS', 1, 510, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_committee_development', '发展建设志工', 'CLASS', 1, 520, UTC_TIMESTAMP(), UTC_TIMESTAMP());

INSERT IGNORE INTO volunteer_position_profiles
    (position_key, system_type, line_type, is_selectable, created_at, updated_at)
VALUES
    ('volunteer_shuku_chair', 'GOVERNANCE', 'GENERAL', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_shuku_learning_vice_chair', 'GOVERNANCE', 'LEARNING', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_shuku_operations_vice_chair', 'GOVERNANCE', 'OPERATIONS', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_shuku_development_vice_chair', 'GOVERNANCE', 'DEVELOPMENT', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_shuku_director', 'GOVERNANCE', 'GENERAL', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_shuku_supervisor_chair', 'GOVERNANCE', 'SUPERVISION', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_shuku_supervisor', 'GOVERNANCE', 'SUPERVISION', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_chair', 'GOVERNANCE', 'GENERAL', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_learning_vice_chair', 'GOVERNANCE', 'LEARNING', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_learning_director', 'GOVERNANCE', 'LEARNING', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_operations_vice_chair', 'GOVERNANCE', 'OPERATIONS', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_operations_director', 'GOVERNANCE', 'OPERATIONS', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_development_vice_chair', 'GOVERNANCE', 'DEVELOPMENT', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_development_director', 'GOVERNANCE', 'DEVELOPMENT', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_center_supervisor', 'GOVERNANCE', 'SUPERVISION', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_committee_learning', 'COMMITTEE_LINE', 'LEARNING', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_committee_operations', 'COMMITTEE_LINE', 'OPERATIONS', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('volunteer_committee_development', 'COMMITTEE_LINE', 'DEVELOPMENT', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP());

CREATE TABLE IF NOT EXISTS volunteer_service_units (
    id VARCHAR(64) PRIMARY KEY,
    unit_code VARCHAR(128) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    system_type VARCHAR(32) NOT NULL,
    line_type VARCHAR(32) NOT NULL,
    parent_id VARCHAR(64) NULL,
    home_shuku_org_unit_id VARCHAR(64) NULL,
    service_target_org_unit_id VARCHAR(64) NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_volunteer_service_unit_system CHECK(system_type IN ('CLASS_TEAM', 'GOVERNANCE', 'COMMITTEE_LINE', 'ACTIVITY')),
    CONSTRAINT chk_volunteer_service_unit_line CHECK(line_type IN ('LEARNING', 'OPERATIONS', 'DEVELOPMENT', 'GENERAL', 'SUPERVISION')),
    CONSTRAINT fk_volunteer_service_unit_parent FOREIGN KEY(parent_id) REFERENCES volunteer_service_units(id),
    CONSTRAINT fk_volunteer_service_unit_home FOREIGN KEY(home_shuku_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_volunteer_service_unit_target FOREIGN KEY(service_target_org_unit_id) REFERENCES org_units(id),
    INDEX idx_volunteer_service_units_parent(parent_id, is_active, sort_order),
    INDEX idx_volunteer_service_units_target(service_target_org_unit_id, is_active),
    INDEX idx_volunteer_service_units_home(home_shuku_org_unit_id, system_type, line_type, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE volunteer_appointments
    ADD COLUMN volunteer_service_unit_id VARCHAR(64) NULL AFTER member_id,
    ADD INDEX idx_volunteer_appointments_service_unit(volunteer_service_unit_id, status),
    ADD CONSTRAINT fk_volunteer_appointments_service_unit
        FOREIGN KEY(volunteer_service_unit_id) REFERENCES volunteer_service_units(id);

CREATE TABLE IF NOT EXISTS volunteer_appointment_recommendation_rules (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_service_unit_id VARCHAR(64) NOT NULL,
    source_position_key VARCHAR(64) NOT NULL,
    target_service_unit_id VARCHAR(64) NOT NULL,
    target_position_key VARCHAR(64) NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_volunteer_recommendation_rule UNIQUE(source_service_unit_id, source_position_key, target_service_unit_id, target_position_key),
    CONSTRAINT fk_volunteer_recommendation_source_unit FOREIGN KEY(source_service_unit_id) REFERENCES volunteer_service_units(id) ON DELETE CASCADE,
    CONSTRAINT fk_volunteer_recommendation_source_position FOREIGN KEY(source_position_key) REFERENCES volunteer_position_catalog(position_key),
    CONSTRAINT fk_volunteer_recommendation_target_unit FOREIGN KEY(target_service_unit_id) REFERENCES volunteer_service_units(id) ON DELETE CASCADE,
    CONSTRAINT fk_volunteer_recommendation_target_position FOREIGN KEY(target_position_key) REFERENCES volunteer_position_catalog(position_key),
    INDEX idx_volunteer_recommendation_source(source_service_unit_id, source_position_key, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS volunteer_appointment_links (
    source_appointment_id BIGINT NOT NULL,
    target_appointment_id BIGINT NOT NULL,
    recommendation_rule_id BIGINT NULL,
    link_type VARCHAR(32) NOT NULL DEFAULT 'RECOMMENDED_PAIR',
    created_at DATETIME NOT NULL,
    PRIMARY KEY(source_appointment_id, target_appointment_id),
    CONSTRAINT chk_volunteer_appointment_link_type CHECK(link_type IN ('RECOMMENDED_PAIR')),
    CONSTRAINT chk_volunteer_appointment_link_distinct CHECK(source_appointment_id <> target_appointment_id),
    CONSTRAINT fk_volunteer_appointment_link_source FOREIGN KEY(source_appointment_id) REFERENCES volunteer_appointments(id) ON DELETE CASCADE,
    CONSTRAINT fk_volunteer_appointment_link_target FOREIGN KEY(target_appointment_id) REFERENCES volunteer_appointments(id) ON DELETE CASCADE,
    CONSTRAINT fk_volunteer_appointment_link_rule FOREIGN KEY(recommendation_rule_id) REFERENCES volunteer_appointment_recommendation_rules(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE wechat_member_bindings
    MODIFY COLUMN member_id BIGINT NULL,
    ADD COLUMN person_id VARCHAR(64) NULL AFTER member_id,
    ADD COLUMN verified_user_id BIGINT NULL AFTER person_id,
    ADD INDEX idx_wechat_member_bindings_person(person_id, status),
    ADD CONSTRAINT fk_wechat_binding_person FOREIGN KEY(person_id) REFERENCES person_profiles(id) ON DELETE SET NULL,
    ADD CONSTRAINT fk_wechat_binding_verified_user FOREIGN KEY(verified_user_id) REFERENCES app_users(id) ON DELETE SET NULL;
