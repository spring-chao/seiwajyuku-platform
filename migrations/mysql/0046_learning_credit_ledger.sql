-- 0046: G5 统一学分账本与学习成果结算 V1（C1-C5）
-- Formal settlement remains behind LEARNING_CREDIT_SETTLEMENT_ENABLED.

CREATE TABLE IF NOT EXISTS learning_credit_rule_versions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    rule_set_key VARCHAR(128) NOT NULL,
    version_label VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    effective_from DATETIME NULL,
    effective_to DATETIME NULL,
    metadata_json TEXT NOT NULL,
    created_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_learning_credit_rule_version_status CHECK(status IN ('DRAFT', 'PUBLISHED', 'RETIRED')),
    CONSTRAINT uq_learning_credit_rule_version UNIQUE(rule_set_key, version_label),
    CONSTRAINT fk_learning_credit_rule_version_user FOREIGN KEY(created_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS learning_credit_rules (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    rule_version_id BIGINT NOT NULL,
    rule_key VARCHAR(128) NOT NULL,
    credit_category VARCHAR(32) NOT NULL,
    credit_type VARCHAR(64) NOT NULL,
    settlement_model VARCHAR(32) NOT NULL,
    points DECIMAL(10,2) NULL,
    cap_points DECIMAL(10,2) NULL,
    rule_snapshot_json TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_learning_credit_rule_category CHECK(credit_category IN ('STANDARD_LEARNING', 'EXTENSION_ACTIVITY')),
    CONSTRAINT chk_learning_credit_rule_model CHECK(settlement_model IN ('DAILY_ONCE', 'MONTHLY_CAP', 'CYCLE_ONCE', 'COURSE_COMPLETION', 'EVENT_ONCE', 'MANUAL_ADJUSTMENT')),
    CONSTRAINT chk_learning_credit_rule_status CHECK(status IN ('ACTIVE', 'DISABLED')),
    CONSTRAINT uq_learning_credit_rule UNIQUE(rule_version_id, rule_key),
    CONSTRAINT fk_learning_credit_rule_version FOREIGN KEY(rule_version_id) REFERENCES learning_credit_rule_versions(id) ON DELETE CASCADE,
    CONSTRAINT fk_learning_credit_rule_user FOREIGN KEY(created_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_learning_credit_rules_version ON learning_credit_rules(rule_version_id, status, rule_key);

CREATE TABLE IF NOT EXISTS learning_credit_entries (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    member_id BIGINT NOT NULL,
    credit_category VARCHAR(32) NOT NULL,
    credit_type VARCHAR(64) NOT NULL,
    points DECIMAL(10,2) NOT NULL,
    source_type VARCHAR(64) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    class_org_unit_id VARCHAR(64) NULL,
    learning_cycle_id BIGINT NULL,
    rule_key VARCHAR(128) NOT NULL,
    rule_version VARCHAR(64) NOT NULL,
    rule_version_id BIGINT NULL,
    rule_snapshot_json TEXT NOT NULL,
    occurred_at DATETIME NOT NULL,
    posted_at DATETIME NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    idempotency_key VARCHAR(255) NOT NULL,
    reversal_of_entry_id BIGINT NULL,
    created_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_learning_credit_entry_category CHECK(credit_category IN ('STANDARD_LEARNING', 'EXTENSION_ACTIVITY')),
    CONSTRAINT chk_learning_credit_entry_status CHECK(status IN ('PENDING', 'POSTED', 'REVERSED', 'VOID')),
    CONSTRAINT uq_learning_credit_entry_idempotency UNIQUE(idempotency_key),
    CONSTRAINT fk_learning_credit_entry_member FOREIGN KEY(member_id) REFERENCES members(id),
    CONSTRAINT fk_learning_credit_entry_class FOREIGN KEY(class_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_learning_credit_entry_cycle FOREIGN KEY(learning_cycle_id) REFERENCES class_learning_cycles(id),
    CONSTRAINT fk_learning_credit_entry_rule_version FOREIGN KEY(rule_version_id) REFERENCES learning_credit_rule_versions(id),
    CONSTRAINT fk_learning_credit_entry_reversal FOREIGN KEY(reversal_of_entry_id) REFERENCES learning_credit_entries(id),
    CONSTRAINT fk_learning_credit_entry_user FOREIGN KEY(created_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_learning_credit_entries_member_time ON learning_credit_entries(member_id, occurred_at, id);
CREATE INDEX idx_learning_credit_entries_member_category ON learning_credit_entries(member_id, credit_category, status);
CREATE INDEX idx_learning_credit_entries_source ON learning_credit_entries(source_type, source_id);
CREATE INDEX idx_learning_credit_entries_cycle ON learning_credit_entries(learning_cycle_id, credit_type, status);

ALTER TABLE class_learning_bindings ADD COLUMN credit_rule_version_id BIGINT NULL;
CREATE INDEX idx_class_learning_bindings_credit_rule ON class_learning_bindings(credit_rule_version_id);

ALTER TABLE study_meeting_courses
    ADD COLUMN completion_status VARCHAR(32) NOT NULL DEFAULT 'UNCONFIRMED',
    ADD COLUMN completed_at DATETIME NULL,
    ADD COLUMN confirmed_by_member_id BIGINT NULL,
    ADD COLUMN confirmed_by_user_id BIGINT NULL,
    ADD COLUMN completion_note VARCHAR(1000) NULL,
    ADD COLUMN credit_rule_version_id BIGINT NULL,
    ADD COLUMN plan_key VARCHAR(128) NULL,
    ADD COLUMN plan_version_label VARCHAR(64) NULL;

CREATE TABLE IF NOT EXISTS study_meeting_course_completions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    study_meeting_course_id BIGINT NOT NULL,
    member_id BIGINT NOT NULL,
    completion_status VARCHAR(32) NOT NULL DEFAULT 'UNCONFIRMED',
    completed_at DATETIME NULL,
    confirmed_by_member_id BIGINT NULL,
    confirmed_by_user_id BIGINT NULL,
    confirmation_source VARCHAR(32) NOT NULL DEFAULT 'MEMBER_SUBMISSION',
    note VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_study_course_completion_status CHECK(completion_status IN ('UNCONFIRMED', 'CONFIRMED', 'NOT_COMPLETED')),
    CONSTRAINT chk_study_course_completion_source CHECK(confirmation_source IN ('MEMBER_SUBMISSION', 'OPERATOR_CONFIRMATION', 'IMPORT')),
    CONSTRAINT uq_study_course_completion UNIQUE(study_meeting_course_id, member_id),
    CONSTRAINT fk_study_course_completion_course FOREIGN KEY(study_meeting_course_id) REFERENCES study_meeting_courses(id) ON DELETE CASCADE,
    CONSTRAINT fk_study_course_completion_member FOREIGN KEY(member_id) REFERENCES members(id),
    CONSTRAINT fk_study_course_completion_confirmed_member FOREIGN KEY(confirmed_by_member_id) REFERENCES members(id),
    CONSTRAINT fk_study_course_completion_confirmed_user FOREIGN KEY(confirmed_by_user_id) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_study_meeting_course_completions_member ON study_meeting_course_completions(member_id, completion_status, updated_at);

INSERT IGNORE INTO learning_credit_rule_versions
    (rule_set_key, version_label, status, effective_from, metadata_json, created_at, updated_at)
VALUES ('STANDARD_3Y_2026', '2026.1', 'PUBLISHED', '2026-01-01',
        '{"source":"course-credit-rules-2026.json","scope":"standard_learning"}',
        UTC_TIMESTAMP(), UTC_TIMESTAMP());
INSERT IGNORE INTO learning_credit_rules
    (rule_version_id, rule_key, credit_category, credit_type, settlement_model, points, cap_points, rule_snapshot_json, created_at, updated_at)
SELECT id, 'DAILY_READING', 'STANDARD_LEARNING', 'DAILY_READING', 'DAILY_ONCE', 1, NULL, '{"points":1,"unit":"WORKDAY"}', UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1';
INSERT IGNORE INTO learning_credit_rules
    (rule_version_id, rule_key, credit_category, credit_type, settlement_model, points, cap_points, rule_snapshot_json, created_at, updated_at)
SELECT id, 'MANUAL_ADJUSTMENT', 'STANDARD_LEARNING', 'MANUAL_ADJUSTMENT', 'MANUAL_ADJUSTMENT', NULL, NULL, '{"points":"FROM_ADJUSTMENT"}', UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1';
INSERT IGNORE INTO learning_credit_rules
    (rule_version_id, rule_key, credit_category, credit_type, settlement_model, points, cap_points, rule_snapshot_json, created_at, updated_at)
SELECT id, 'EXCELLENT_SHARE', 'STANDARD_LEARNING', 'EXCELLENT_SHARE', 'MONTHLY_CAP', 1, 5, '{"points":1,"cap_points":5,"period":"MONTH"}', UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1';
INSERT IGNORE INTO learning_credit_rules
    (rule_version_id, rule_key, credit_category, credit_type, settlement_model, points, cap_points, rule_snapshot_json, created_at, updated_at)
SELECT id, 'GROUP_MEETING_ATTENDANCE', 'STANDARD_LEARNING', 'GROUP_MEETING_ATTENDANCE', 'CYCLE_ONCE', 4, NULL, '{"points":4,"unit":"PERSON_CYCLE"}', UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1';
INSERT IGNORE INTO learning_credit_rules
    (rule_version_id, rule_key, credit_category, credit_type, settlement_model, points, cap_points, rule_snapshot_json, created_at, updated_at)
SELECT id, 'COURSE_COMPLETION', 'STANDARD_LEARNING', 'COURSE_COMPLETION', 'COURSE_COMPLETION', NULL, NULL, '{"points":"FROM_PLAN_COURSE_RULE"}', UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1';

UPDATE class_learning_bindings b
JOIN learning_plan_versions p ON p.id=b.plan_version_id
JOIN learning_credit_rule_versions v ON v.rule_set_key=p.plan_key AND v.version_label=p.version_label
SET b.credit_rule_version_id=v.id
WHERE b.credit_rule_version_id IS NULL;

INSERT IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('plans:credit_settlement_preview', '预览学分结算与对账', 'INTERNAL', UTC_TIMESTAMP()),
       ('plans:credit_settlement_manage', '正式结算与学分冲销', 'SENSITIVE', UTC_TIMESTAMP());
INSERT IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'plans:credit_settlement_preview' FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'ops_center_learning', 'ops_center_management');
INSERT IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'plans:credit_settlement_manage' FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'ops_center_learning', 'ops_center_management');
