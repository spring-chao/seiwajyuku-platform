-- 0049: C7 daily-reading, excellent-share facts, and configurable mainland-China calendars.
-- This migration creates no learning-credit ledger entries.

CREATE TABLE IF NOT EXISTS learning_business_calendar_versions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    calendar_key VARCHAR(64) NOT NULL DEFAULT 'CHINA_MAINLAND',
    calendar_year INT NOT NULL,
    version_label VARCHAR(64) NOT NULL,
    timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    created_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_learning_calendar_year CHECK(calendar_year BETWEEN 2000 AND 2100),
    CONSTRAINT chk_learning_calendar_timezone CHECK(timezone='Asia/Shanghai'),
    CONSTRAINT chk_learning_calendar_status CHECK(status IN ('DRAFT', 'PUBLISHED', 'RETIRED')),
    CONSTRAINT uq_learning_calendar_version UNIQUE(calendar_key, calendar_year, version_label),
    CONSTRAINT fk_learning_calendar_version_user FOREIGN KEY(created_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_learning_calendar_versions_lookup
    ON learning_business_calendar_versions(calendar_key, calendar_year, status, id);

CREATE TABLE IF NOT EXISTS learning_business_calendar_days (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    calendar_version_id BIGINT NOT NULL,
    business_date DATE NOT NULL,
    day_type VARCHAR(32) NOT NULL,
    note VARCHAR(500) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_learning_calendar_day_type CHECK(day_type IN ('NORMAL_WORKDAY', 'WEEKEND', 'HOLIDAY', 'ADJUSTED_WORKDAY')),
    CONSTRAINT uq_learning_calendar_day UNIQUE(calendar_version_id, business_date),
    CONSTRAINT fk_learning_calendar_day_version FOREIGN KEY(calendar_version_id) REFERENCES learning_business_calendar_versions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_learning_calendar_days_lookup
    ON learning_business_calendar_days(calendar_version_id, business_date);

CREATE TABLE IF NOT EXISTS learning_credit_activity_facts (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    activity_type VARCHAR(32) NOT NULL,
    member_id BIGINT NOT NULL,
    class_org_unit_id VARCHAR(64) NOT NULL,
    binding_id BIGINT NULL,
    occurred_on DATE NOT NULL,
    participation_status VARCHAR(32) NOT NULL DEFAULT 'RECORDED',
    source_type VARCHAR(128) NOT NULL,
    source_id VARCHAR(255) NOT NULL,
    title VARCHAR(255) NULL,
    metadata_json TEXT NOT NULL,
    created_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_learning_activity_type CHECK(activity_type IN ('DAILY_READING', 'EXCELLENT_SHARE')),
    CONSTRAINT chk_learning_activity_status CHECK(participation_status IN ('RECORDED', 'CONFIRMED', 'REJECTED', 'CANCELLED')),
    CONSTRAINT uq_learning_activity_source UNIQUE(source_type, source_id),
    CONSTRAINT fk_learning_activity_member FOREIGN KEY(member_id) REFERENCES members(id),
    CONSTRAINT fk_learning_activity_class FOREIGN KEY(class_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_learning_activity_binding FOREIGN KEY(binding_id) REFERENCES class_learning_bindings(id) ON DELETE SET NULL,
    CONSTRAINT fk_learning_activity_user FOREIGN KEY(created_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_learning_activity_facts_member_date
    ON learning_credit_activity_facts(member_id, occurred_on, activity_type, id);
CREATE INDEX idx_learning_activity_facts_class_date
    ON learning_credit_activity_facts(class_org_unit_id, occurred_on, activity_type, id);
CREATE INDEX idx_learning_activity_facts_binding
    ON learning_credit_activity_facts(binding_id, occurred_on, activity_type);

INSERT IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('plans:business_calendar_manage', '维护年度工作日日历', 'SENSITIVE', UTC_TIMESTAMP()),
       ('plans:credit_activity_fact_manage', '维护每日读书与优秀分享事实', 'SENSITIVE', UTC_TIMESTAMP());
INSERT IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'plans:business_calendar_manage' FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'ops_center_learning', 'ops_center_management');
INSERT IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'plans:credit_activity_fact_manage' FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'ops_center_learning', 'ops_center_management');
