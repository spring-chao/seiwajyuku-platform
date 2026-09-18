-- 0065: G5.4 C1 historical credit import staging.
--
-- This schema records an immutable source workbook, parsed rows, and
-- proposed historical items.  It intentionally has no operation that posts
-- these items to learning_credit_entries.

START TRANSACTION;

CREATE TABLE IF NOT EXISTS learning_credit_import_batches (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    import_type VARCHAR(64) NOT NULL,
    source_year INT NOT NULL,
    source_name VARCHAR(255) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    file_sha256 CHAR(64) NOT NULL,
    source_rule_version VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'UPLOADED',
    metadata_json TEXT NOT NULL,
    created_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_learning_credit_import_batch_status CHECK(status IN ('UPLOADED', 'PARSED', 'NEEDS_REVIEW', 'RECONCILED', 'APPROVED', 'POSTED', 'CANCELLED')),
    CONSTRAINT chk_learning_credit_import_batch_year CHECK(source_year BETWEEN 2000 AND 2100),
    CONSTRAINT uq_learning_credit_import_batch_file UNIQUE(file_sha256, import_type),
    CONSTRAINT fk_learning_credit_import_batch_user FOREIGN KEY(created_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_learning_credit_import_batch_status
    ON learning_credit_import_batches(import_type, source_year, status, id);

CREATE TABLE IF NOT EXISTS learning_credit_import_rows (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    batch_id BIGINT NOT NULL,
    source_sheet VARCHAR(255) NOT NULL,
    source_row_number INT NOT NULL,
    raw_name VARCHAR(255) NOT NULL,
    raw_class_name VARCHAR(255) NULL,
    raw_group_name VARCHAR(255) NULL,
    raw_total_points DECIMAL(10,2) NULL,
    calculated_total_points DECIMAL(10,2) NULL,
    matched_member_id BIGINT NULL,
    match_status VARCHAR(32) NOT NULL DEFAULT 'NOT_RUN',
    validation_status VARCHAR(32) NOT NULL,
    review_status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    match_reason VARCHAR(255) NULL,
    metadata_json TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_learning_credit_import_row_number CHECK(source_row_number > 0),
    CONSTRAINT chk_learning_credit_import_row_match CHECK(match_status IN ('NOT_RUN', 'AUTO_MATCHED', 'CONFIRMED', 'AMBIGUOUS', 'NOT_FOUND', 'CONFLICT')),
    CONSTRAINT chk_learning_credit_import_row_validation CHECK(validation_status IN ('PASS', 'TOTAL_MISSING', 'DETAIL_MISSING', 'MISMATCH', 'ZERO')),
    CONSTRAINT chk_learning_credit_import_row_review CHECK(review_status IN ('PENDING', 'REVIEW_REQUIRED', 'CONFIRMED', 'REJECTED', 'NO_CREDIT')),
    CONSTRAINT fk_learning_credit_import_row_batch FOREIGN KEY(batch_id) REFERENCES learning_credit_import_batches(id) ON DELETE CASCADE,
    CONSTRAINT fk_learning_credit_import_row_member FOREIGN KEY(matched_member_id) REFERENCES members(id),
    CONSTRAINT uq_learning_credit_import_row_location UNIQUE(batch_id, source_sheet, source_row_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_learning_credit_import_row_review
    ON learning_credit_import_rows(batch_id, validation_status, review_status, id);

CREATE TABLE IF NOT EXISTS learning_credit_import_items (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    batch_id BIGINT NOT NULL,
    import_row_id BIGINT NOT NULL,
    matched_member_id BIGINT NULL,
    source_sheet VARCHAR(255) NOT NULL,
    source_row_number INT NOT NULL,
    source_column_index INT NOT NULL,
    source_column_name VARCHAR(255) NOT NULL,
    credit_category VARCHAR(32) NOT NULL,
    legacy_credit_type VARCHAR(64) NOT NULL,
    source_month INT NULL,
    accounting_month CHAR(7) NULL,
    points DECIMAL(10,2) NOT NULL,
    source_rule_version VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING_REVIEW',
    metadata_json TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_learning_credit_import_item_row CHECK(source_row_number > 0),
    CONSTRAINT chk_learning_credit_import_item_column CHECK(source_column_index > 0),
    CONSTRAINT chk_learning_credit_import_item_month CHECK(source_month IS NULL OR source_month BETWEEN 1 AND 12),
    CONSTRAINT chk_learning_credit_import_item_points CHECK(points > 0),
    CONSTRAINT chk_learning_credit_import_item_category CHECK(credit_category IN ('STANDARD_LEARNING', 'EXTENSION_ACTIVITY')),
    CONSTRAINT chk_learning_credit_import_item_type CHECK(legacy_credit_type IN ('LEGACY_READING_AND_SHARE', 'LEGACY_CLASS_MEETING', 'LEGACY_GROUP_MEETING', 'LEGACY_ONLINE_COURSE', 'LEGACY_OFFLINE_COURSE', 'LEGACY_REPORT_EVENT', 'LEGACY_STUDY_TOUR')),
    CONSTRAINT chk_learning_credit_import_item_status CHECK(status IN ('PENDING_REVIEW', 'READY', 'POSTED', 'CANCELLED')),
    CONSTRAINT fk_learning_credit_import_item_batch FOREIGN KEY(batch_id) REFERENCES learning_credit_import_batches(id) ON DELETE CASCADE,
    CONSTRAINT fk_learning_credit_import_item_row FOREIGN KEY(import_row_id) REFERENCES learning_credit_import_rows(id) ON DELETE CASCADE,
    CONSTRAINT fk_learning_credit_import_item_member FOREIGN KEY(matched_member_id) REFERENCES members(id),
    CONSTRAINT uq_learning_credit_import_item_cell UNIQUE(batch_id, import_row_id, source_sheet, source_column_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_learning_credit_import_item_dry_run
    ON learning_credit_import_items(batch_id, matched_member_id, source_month, status, id);

INSERT IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('plans:historical_credit_import_manage', '管理历史学分导入预览', 'SENSITIVE', UTC_TIMESTAMP());
INSERT IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'plans:historical_credit_import_manage'
FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'ops_center_learning', 'ops_center_management');

COMMIT;
