-- 0050: C7.1.2 总部每日读书导入批次、来源身份与观察记录
-- 这里只保存可追溯的导入事实，不创建 learning_credit_entries 学分流水。

CREATE TABLE IF NOT EXISTS hq_reading_import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL DEFAULT 'HQ_READING_EXPORT'
        CHECK(source_type='HQ_READING_EXPORT'),
    target_class_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    original_filename TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    imported_by INTEGER REFERENCES app_users(id),
    row_count INTEGER NOT NULL DEFAULT 0 CHECK(row_count >= 0),
    unique_person_count INTEGER NOT NULL DEFAULT 0 CHECK(unique_person_count >= 0),
    date_from TEXT,
    date_to TEXT,
    status TEXT NOT NULL DEFAULT 'PARSED'
        CHECK(status IN ('PARSED', 'MATCHING', 'CONFIRMED', 'DRY_RUN', 'REJECTED')),
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_type, target_class_org_unit_id, file_sha256)
);
CREATE INDEX IF NOT EXISTS idx_hq_reading_batches_class_date
    ON hq_reading_import_batches(target_class_org_unit_id, date_from, date_to, id);

CREATE TABLE IF NOT EXISTS hq_reading_source_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_system TEXT NOT NULL DEFAULT 'HQ',
    source_identity_key TEXT NOT NULL,
    target_class_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    member_id INTEGER NOT NULL REFERENCES members(id),
    first_confirmed_at TEXT NOT NULL,
    first_confirmed_by INTEGER REFERENCES app_users(id),
    latest_seen_name TEXT NOT NULL,
    latest_seen_masked_account TEXT,
    latest_seen_group TEXT,
    status TEXT NOT NULL DEFAULT 'CONFIRMED'
        CHECK(status IN ('CONFIRMED', 'CONFLICT')),
    conflict_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_system, source_identity_key)
);
CREATE INDEX IF NOT EXISTS idx_hq_reading_source_identity_member
    ON hq_reading_source_identities(target_class_org_unit_id, member_id, status);

CREATE TABLE IF NOT EXISTS hq_reading_import_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES hq_reading_import_batches(id),
    first_batch_id INTEGER NOT NULL REFERENCES hq_reading_import_batches(id),
    source_type TEXT NOT NULL DEFAULT 'HQ_READING_EXPORT'
        CHECK(source_type='HQ_READING_EXPORT'),
    source_id TEXT NOT NULL,
    source_identity_key TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    source_row_number INTEGER NOT NULL CHECK(source_row_number > 0),
    target_class_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    member_id INTEGER REFERENCES members(id),
    source_name TEXT NOT NULL,
    source_masked_account TEXT,
    source_group_name TEXT,
    mapped_group_org_unit_id TEXT REFERENCES org_units(id),
    group_type TEXT NOT NULL
        CHECK(group_type IN ('CLASS_REGULAR', 'CLASS_ADVANCED', 'NO_GROUP', 'UNKNOWN')),
    source_is_staff INTEGER NOT NULL DEFAULT 0 CHECK(source_is_staff IN (0, 1)),
    learning_qualification_status TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK(learning_qualification_status IN ('LEARNING', 'NOT_LEARNING', 'UNKNOWN')),
    recording_raw TEXT,
    recording_status TEXT NOT NULL
        CHECK(recording_status IN ('COMPLETE', 'NOT_COMPLETE', 'UNKNOWN')),
    occurred_on TEXT NOT NULL,
    match_status TEXT NOT NULL
        CHECK(match_status IN (
            'AUTO_MATCHED', 'CANDIDATE', 'CONFIRMED_BINDING',
            'MEMBER_MAPPING_REQUIRED', 'SOURCE_IDENTITY_CONFLICT',
            'GROUP_MAPPING_MISSING', 'GROUP_MISMATCH', 'REVIEW_REQUIRED',
            'NO_GROUP'
        )),
    match_method TEXT,
    personal_credit_eligible INTEGER NOT NULL DEFAULT 0
        CHECK(personal_credit_eligible IN (0, 1)),
    class_rate_denominator_eligible INTEGER NOT NULL DEFAULT 0
        CHECK(class_rate_denominator_eligible IN (0, 1)),
    class_rate_numerator_eligible INTEGER NOT NULL DEFAULT 0
        CHECK(class_rate_numerator_eligible IN (0, 1)),
    eligibility_reason TEXT,
    content_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    seen_count INTEGER NOT NULL DEFAULT 1 CHECK(seen_count > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_type, source_id)
);
CREATE INDEX IF NOT EXISTS idx_hq_reading_observations_batch
    ON hq_reading_import_observations(batch_id, occurred_on, id);
CREATE INDEX IF NOT EXISTS idx_hq_reading_observations_class_date
    ON hq_reading_import_observations(target_class_org_unit_id, occurred_on, group_type, id);
CREATE INDEX IF NOT EXISTS idx_hq_reading_observations_identity
    ON hq_reading_import_observations(source_identity_key, occurred_on, id);

INSERT OR IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('plans:hq_reading_import_manage', '导入总部每日读书并进行身份核验', 'SENSITIVE', datetime('now'));
INSERT OR IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'plans:hq_reading_import_manage'
FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'ops_center_learning', 'ops_center_management');
