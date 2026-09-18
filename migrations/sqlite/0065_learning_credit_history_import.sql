-- 0065: G5.4 C1 historical credit import staging.
-- This schema stores source rows and proposed items only; it never posts a ledger entry.

BEGIN;

CREATE TABLE IF NOT EXISTS learning_credit_import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_type TEXT NOT NULL,
    source_year INTEGER NOT NULL CHECK(source_year BETWEEN 2000 AND 2100),
    source_name TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    source_rule_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'UPLOADED'
        CHECK(status IN ('UPLOADED', 'PARSED', 'NEEDS_REVIEW', 'RECONCILED', 'APPROVED', 'POSTED', 'CANCELLED')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(file_sha256, import_type)
);
CREATE INDEX IF NOT EXISTS idx_learning_credit_import_batch_status
    ON learning_credit_import_batches(import_type, source_year, status, id);

CREATE TABLE IF NOT EXISTS learning_credit_import_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES learning_credit_import_batches(id) ON DELETE CASCADE,
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL CHECK(source_row_number > 0),
    raw_name TEXT NOT NULL,
    raw_class_name TEXT,
    raw_group_name TEXT,
    raw_total_points REAL,
    calculated_total_points REAL,
    matched_member_id INTEGER REFERENCES members(id),
    match_status TEXT NOT NULL DEFAULT 'NOT_RUN'
        CHECK(match_status IN ('NOT_RUN', 'AUTO_MATCHED', 'CONFIRMED', 'AMBIGUOUS', 'NOT_FOUND', 'CONFLICT')),
    validation_status TEXT NOT NULL
        CHECK(validation_status IN ('PASS', 'TOTAL_MISSING', 'DETAIL_MISSING', 'MISMATCH', 'ZERO')),
    review_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(review_status IN ('PENDING', 'REVIEW_REQUIRED', 'CONFIRMED', 'REJECTED', 'NO_CREDIT')),
    match_reason TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(batch_id, source_sheet, source_row_number)
);
CREATE INDEX IF NOT EXISTS idx_learning_credit_import_row_review
    ON learning_credit_import_rows(batch_id, validation_status, review_status, id);

CREATE TABLE IF NOT EXISTS learning_credit_import_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES learning_credit_import_batches(id) ON DELETE CASCADE,
    import_row_id INTEGER NOT NULL REFERENCES learning_credit_import_rows(id) ON DELETE CASCADE,
    matched_member_id INTEGER REFERENCES members(id),
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL CHECK(source_row_number > 0),
    source_column_index INTEGER NOT NULL CHECK(source_column_index > 0),
    source_column_name TEXT NOT NULL,
    credit_category TEXT NOT NULL
        CHECK(credit_category IN ('STANDARD_LEARNING', 'EXTENSION_ACTIVITY')),
    legacy_credit_type TEXT NOT NULL
        CHECK(legacy_credit_type IN ('LEGACY_READING_AND_SHARE', 'LEGACY_CLASS_MEETING', 'LEGACY_GROUP_MEETING', 'LEGACY_ONLINE_COURSE', 'LEGACY_OFFLINE_COURSE', 'LEGACY_REPORT_EVENT', 'LEGACY_STUDY_TOUR')),
    source_month INTEGER CHECK(source_month IS NULL OR source_month BETWEEN 1 AND 12),
    accounting_month TEXT,
    points REAL NOT NULL CHECK(points > 0),
    source_rule_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK(status IN ('PENDING_REVIEW', 'READY', 'POSTED', 'CANCELLED')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(batch_id, import_row_id, source_sheet, source_column_index)
);
CREATE INDEX IF NOT EXISTS idx_learning_credit_import_item_dry_run
    ON learning_credit_import_items(batch_id, matched_member_id, source_month, status, id);

INSERT OR IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('plans:historical_credit_import_manage', '管理历史学分导入预览', 'SENSITIVE', datetime('now'));
INSERT OR IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'plans:historical_credit_import_manage'
FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'ops_center_learning', 'ops_center_management');

COMMIT;
