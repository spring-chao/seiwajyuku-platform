-- 0046: G5 统一学分账本与学习成果结算 V1（C1-C5）
-- 本迁移只建立可追溯模型和事实存储；正式结算仍由
-- LEARNING_CREDIT_SETTLEMENT_ENABLED 独立控制，默认关闭。

CREATE TABLE IF NOT EXISTS learning_credit_rule_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_set_key TEXT NOT NULL,
    version_label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK(status IN ('DRAFT', 'PUBLISHED', 'RETIRED')),
    effective_from TEXT,
    effective_to TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(rule_set_key, version_label)
);

CREATE TABLE IF NOT EXISTS learning_credit_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_version_id INTEGER NOT NULL REFERENCES learning_credit_rule_versions(id) ON DELETE CASCADE,
    rule_key TEXT NOT NULL,
    credit_category TEXT NOT NULL
        CHECK(credit_category IN ('STANDARD_LEARNING', 'EXTENSION_ACTIVITY')),
    credit_type TEXT NOT NULL,
    settlement_model TEXT NOT NULL
        CHECK(settlement_model IN (
            'DAILY_ONCE', 'MONTHLY_CAP', 'CYCLE_ONCE',
            'COURSE_COMPLETION', 'EVENT_ONCE', 'MANUAL_ADJUSTMENT'
        )),
    points REAL,
    cap_points REAL,
    rule_snapshot_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK(status IN ('ACTIVE', 'DISABLED')),
    created_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(rule_version_id, rule_key)
);
CREATE INDEX IF NOT EXISTS idx_learning_credit_rules_version
    ON learning_credit_rules(rule_version_id, status, rule_key);

CREATE TABLE IF NOT EXISTS learning_credit_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id),
    credit_category TEXT NOT NULL
        CHECK(credit_category IN ('STANDARD_LEARNING', 'EXTENSION_ACTIVITY')),
    credit_type TEXT NOT NULL,
    points REAL NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    class_org_unit_id TEXT REFERENCES org_units(id),
    learning_cycle_id INTEGER REFERENCES class_learning_cycles(id),
    rule_key TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    rule_version_id INTEGER REFERENCES learning_credit_rule_versions(id),
    rule_snapshot_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    posted_at TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(status IN ('PENDING', 'POSTED', 'REVERSED', 'VOID')),
    idempotency_key TEXT NOT NULL UNIQUE,
    reversal_of_entry_id INTEGER REFERENCES learning_credit_entries(id),
    created_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_credit_entries_member_time
    ON learning_credit_entries(member_id, occurred_at, id);
CREATE INDEX IF NOT EXISTS idx_learning_credit_entries_member_category
    ON learning_credit_entries(member_id, credit_category, status);
CREATE INDEX IF NOT EXISTS idx_learning_credit_entries_source
    ON learning_credit_entries(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_learning_credit_entries_cycle
    ON learning_credit_entries(learning_cycle_id, credit_type, status);

-- The plan version is the historical anchor.  This optional link is filled
-- for bindings whose corresponding generic rule version already exists.
ALTER TABLE class_learning_bindings ADD COLUMN credit_rule_version_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_class_learning_bindings_credit_rule
    ON class_learning_bindings(credit_rule_version_id);

-- C3: a course snapshot is still a session-level fact, while this table is
-- the authoritative per-member completion fact used by course settlement.
ALTER TABLE study_meeting_courses ADD COLUMN completion_status TEXT NOT NULL DEFAULT 'UNCONFIRMED';
ALTER TABLE study_meeting_courses ADD COLUMN completed_at TEXT;
ALTER TABLE study_meeting_courses ADD COLUMN confirmed_by_member_id INTEGER REFERENCES members(id);
ALTER TABLE study_meeting_courses ADD COLUMN confirmed_by_user_id INTEGER REFERENCES app_users(id);
ALTER TABLE study_meeting_courses ADD COLUMN completion_note TEXT;
ALTER TABLE study_meeting_courses ADD COLUMN credit_rule_version_id INTEGER;
ALTER TABLE study_meeting_courses ADD COLUMN plan_key TEXT;
ALTER TABLE study_meeting_courses ADD COLUMN plan_version_label TEXT;

CREATE TABLE IF NOT EXISTS study_meeting_course_completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_meeting_course_id INTEGER NOT NULL REFERENCES study_meeting_courses(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id),
    completion_status TEXT NOT NULL DEFAULT 'UNCONFIRMED'
        CHECK(completion_status IN ('UNCONFIRMED', 'CONFIRMED', 'NOT_COMPLETED')),
    completed_at TEXT,
    confirmed_by_member_id INTEGER REFERENCES members(id),
    confirmed_by_user_id INTEGER REFERENCES app_users(id),
    confirmation_source TEXT NOT NULL DEFAULT 'MEMBER_SUBMISSION'
        CHECK(confirmation_source IN ('MEMBER_SUBMISSION', 'OPERATOR_CONFIRMATION', 'IMPORT')),
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(study_meeting_course_id, member_id)
);
CREATE INDEX IF NOT EXISTS idx_study_meeting_course_completions_member
    ON study_meeting_course_completions(member_id, completion_status, updated_at);

-- The approved 2026 base policy is publishable independently of the still
-- editable plan-course catalog.  Course settlement additionally requires the
-- matching learning_plan_credit_rule_versions row to be PUBLISHED.
INSERT OR IGNORE INTO learning_credit_rule_versions
    (rule_set_key, version_label, status, effective_from, metadata_json, created_at, updated_at)
VALUES
    ('STANDARD_3Y_2026', '2026.1', 'PUBLISHED', '2026-01-01',
     '{"source":"course-credit-rules-2026.json","scope":"standard_learning"}',
     datetime('now'), datetime('now'));

INSERT OR IGNORE INTO learning_credit_rules
    (rule_version_id, rule_key, credit_category, credit_type, settlement_model,
     points, cap_points, rule_snapshot_json, created_at, updated_at)
SELECT id, 'DAILY_READING', 'STANDARD_LEARNING', 'DAILY_READING', 'DAILY_ONCE',
       1, NULL, '{"points":1,"unit":"WORKDAY"}', datetime('now'), datetime('now')
FROM learning_credit_rule_versions
WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1';
INSERT OR IGNORE INTO learning_credit_rules
    (rule_version_id, rule_key, credit_category, credit_type, settlement_model,
     points, cap_points, rule_snapshot_json, created_at, updated_at)
SELECT id, 'MANUAL_ADJUSTMENT', 'STANDARD_LEARNING', 'MANUAL_ADJUSTMENT', 'MANUAL_ADJUSTMENT',
       NULL, NULL, '{"points":"FROM_ADJUSTMENT"}', datetime('now'), datetime('now')
FROM learning_credit_rule_versions
WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1';
INSERT OR IGNORE INTO learning_credit_rules
    (rule_version_id, rule_key, credit_category, credit_type, settlement_model,
     points, cap_points, rule_snapshot_json, created_at, updated_at)
SELECT id, 'EXCELLENT_SHARE', 'STANDARD_LEARNING', 'EXCELLENT_SHARE', 'MONTHLY_CAP',
       1, 5, '{"points":1,"cap_points":5,"period":"MONTH"}', datetime('now'), datetime('now')
FROM learning_credit_rule_versions
WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1';
INSERT OR IGNORE INTO learning_credit_rules
    (rule_version_id, rule_key, credit_category, credit_type, settlement_model,
     points, cap_points, rule_snapshot_json, created_at, updated_at)
SELECT id, 'GROUP_MEETING_ATTENDANCE', 'STANDARD_LEARNING', 'GROUP_MEETING_ATTENDANCE', 'CYCLE_ONCE',
       4, NULL, '{"points":4,"unit":"PERSON_CYCLE"}', datetime('now'), datetime('now')
FROM learning_credit_rule_versions
WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1';
INSERT OR IGNORE INTO learning_credit_rules
    (rule_version_id, rule_key, credit_category, credit_type, settlement_model,
     points, cap_points, rule_snapshot_json, created_at, updated_at)
SELECT id, 'COURSE_COMPLETION', 'STANDARD_LEARNING', 'COURSE_COMPLETION', 'COURSE_COMPLETION',
       NULL, NULL, '{"points":"FROM_PLAN_COURSE_RULE"}', datetime('now'), datetime('now')
FROM learning_credit_rule_versions
WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1';

UPDATE class_learning_bindings
SET credit_rule_version_id=(
    SELECT v.id
    FROM learning_plan_versions p
    JOIN learning_credit_rule_versions v
      ON v.rule_set_key=p.plan_key AND v.version_label=p.version_label
    WHERE p.id=class_learning_bindings.plan_version_id
)
WHERE credit_rule_version_id IS NULL;

INSERT OR IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('plans:credit_settlement_preview', '预览学分结算与对账', 'INTERNAL', datetime('now'));
INSERT OR IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('plans:credit_settlement_manage', '正式结算与学分冲销', 'SENSITIVE', datetime('now'));
INSERT OR IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'plans:credit_settlement_preview'
FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'ops_center_learning', 'ops_center_management');
INSERT OR IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'plans:credit_settlement_manage'
FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'ops_center_learning', 'ops_center_management');
