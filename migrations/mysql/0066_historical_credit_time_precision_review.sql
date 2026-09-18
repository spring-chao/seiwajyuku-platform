-- 0066: G5.4 C2B/C2C historical review workbench and period precision.
-- No statement in this migration posts a ledger entry.
START TRANSACTION;

ALTER TABLE learning_credit_entries
    MODIFY occurred_at DATETIME NULL,
    ADD COLUMN occurred_precision VARCHAR(16) NOT NULL DEFAULT 'EXACT_DATE',
    ADD COLUMN occurred_year INT NULL,
    ADD COLUMN occurred_month INT NULL;
UPDATE learning_credit_entries
SET occurred_year=YEAR(occurred_at), occurred_month=MONTH(occurred_at)
WHERE occurred_at IS NOT NULL;
ALTER TABLE learning_credit_entries
    MODIFY occurred_year INT NOT NULL,
    ADD CONSTRAINT chk_learning_credit_entry_precision CHECK(occurred_precision IN ('EXACT_DATE', 'MONTH', 'YEAR')),
    ADD CONSTRAINT chk_learning_credit_entry_year CHECK(occurred_year BETWEEN 2000 AND 2100),
    ADD CONSTRAINT chk_learning_credit_entry_month CHECK(occurred_month IS NULL OR occurred_month BETWEEN 1 AND 12),
    ADD CONSTRAINT chk_learning_credit_entry_period CHECK(
        (occurred_precision='EXACT_DATE' AND occurred_at IS NOT NULL AND occurred_month IS NOT NULL)
        OR (occurred_precision='MONTH' AND occurred_at IS NULL AND occurred_month IS NOT NULL)
        OR (occurred_precision='YEAR' AND occurred_at IS NULL AND occurred_month IS NULL)
    );
CREATE INDEX idx_learning_credit_entries_member_period
    ON learning_credit_entries(member_id, occurred_year, occurred_month, occurred_at, id);

ALTER TABLE learning_credit_import_rows
    ADD COLUMN resolved_total_points DECIMAL(10,2) NULL,
    ADD COLUMN credit_review_status VARCHAR(32) NOT NULL DEFAULT 'NOT_REQUIRED',
    ADD COLUMN credit_review_reason VARCHAR(255) NULL,
    ADD COLUMN credit_reviewed_by BIGINT NULL,
    ADD COLUMN credit_reviewed_at DATETIME NULL,
    ADD COLUMN match_snapshot_id VARCHAR(128) NULL,
    ADD COLUMN match_snapshot_fingerprint CHAR(64) NULL,
    ADD COLUMN match_algorithm_version VARCHAR(64) NULL,
    ADD CONSTRAINT chk_learning_credit_import_row_credit_review CHECK(credit_review_status IN ('NOT_REQUIRED', 'PENDING', 'APPROVED', 'NO_CREDIT_CONFIRMED', 'REJECTED', 'NEEDS_SOURCE_CORRECTION')),
    ADD CONSTRAINT fk_learning_credit_import_row_credit_reviewer FOREIGN KEY(credit_reviewed_by) REFERENCES app_users(id);
UPDATE learning_credit_import_rows
SET credit_review_status=CASE WHEN validation_status='PASS' THEN 'NOT_REQUIRED' ELSE 'PENDING' END;

ALTER TABLE learning_credit_import_items
    ADD COLUMN occurred_precision VARCHAR(16) NOT NULL DEFAULT 'YEAR',
    ADD COLUMN occurred_year INT NOT NULL DEFAULT 2026,
    ADD COLUMN occurred_month INT NULL,
    ADD COLUMN period_review_status VARCHAR(32) NOT NULL DEFAULT 'PERIOD_REVIEW_REQUIRED',
    ADD CONSTRAINT chk_learning_credit_import_item_precision CHECK(occurred_precision IN ('EXACT_DATE', 'MONTH', 'YEAR')),
    ADD CONSTRAINT chk_learning_credit_import_item_year CHECK(occurred_year BETWEEN 2000 AND 2100),
    ADD CONSTRAINT chk_learning_credit_import_item_period_month CHECK(occurred_month IS NULL OR occurred_month BETWEEN 1 AND 12),
    ADD CONSTRAINT chk_learning_credit_import_item_period_status CHECK(period_review_status IN ('READY', 'MONTH_CONFIRMED', 'YEAR_ACCEPTED', 'PERIOD_REVIEW_REQUIRED', 'DUAL_TRACK_PENDING', 'REJECTED'));
UPDATE learning_credit_import_items i
JOIN learning_credit_import_batches b ON b.id=i.batch_id
SET i.occurred_year=b.source_year,
    i.occurred_month=i.source_month,
    i.occurred_precision=CASE WHEN i.source_month IS NULL THEN 'YEAR' ELSE 'MONTH' END,
    i.period_review_status=CASE
        WHEN i.source_month=9 THEN 'DUAL_TRACK_PENDING'
        WHEN i.source_month BETWEEN 1 AND 8 THEN 'READY'
        ELSE 'PERIOD_REVIEW_REQUIRED'
    END;

CREATE TABLE IF NOT EXISTS learning_credit_import_class_mappings (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    batch_id BIGINT NOT NULL,
    source_sheet VARCHAR(255) NOT NULL,
    raw_class_name VARCHAR(255) NULL,
    candidate_org_unit_ids_json TEXT NOT NULL,
    confirmed_org_unit_id VARCHAR(64) NULL,
    mapping_status VARCHAR(32) NOT NULL DEFAULT 'PENDING_REVIEW',
    mapping_reason VARCHAR(255) NOT NULL,
    snapshot_id VARCHAR(128) NULL,
    snapshot_fingerprint CHAR(64) NULL,
    evidence_json TEXT NOT NULL,
    confirmed_by BIGINT NULL,
    confirmed_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_learning_credit_import_class_mapping_status CHECK(mapping_status IN ('PENDING_REVIEW', 'EXACT', 'AUTO_RESOLVED', 'CONFIRMED_ALIAS', 'AMBIGUOUS', 'NOT_FOUND', 'REJECTED')),
    CONSTRAINT fk_learning_credit_import_class_mapping_batch FOREIGN KEY(batch_id) REFERENCES learning_credit_import_batches(id) ON DELETE CASCADE,
    CONSTRAINT fk_learning_credit_import_class_mapping_user FOREIGN KEY(confirmed_by) REFERENCES app_users(id),
    CONSTRAINT uq_learning_credit_import_class_mapping UNIQUE(batch_id, source_sheet, raw_class_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_learning_credit_import_class_mapping_review
    ON learning_credit_import_class_mappings(batch_id, mapping_status, source_sheet, raw_class_name);

CREATE TABLE IF NOT EXISTS learning_credit_import_decisions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    batch_id BIGINT NOT NULL,
    decision_type VARCHAR(64) NOT NULL,
    target_type VARCHAR(64) NOT NULL,
    target_id VARCHAR(255) NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    snapshot_id VARCHAR(128) NULL,
    snapshot_fingerprint CHAR(64) NULL,
    algorithm_version VARCHAR(64) NULL,
    actor_user_id BIGINT NULL,
    reason VARCHAR(1000) NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT chk_learning_credit_import_decision_type CHECK(decision_type IN ('CLASS_MAPPING_CONFIRM', 'MEMBER_MATCH_CONFIRM', 'AUTO_MATCH_BULK_CONFIRM', 'CREDIT_TOTAL_APPROVE', 'CREDIT_TOTAL_REJECT', 'CREDIT_SOURCE_CORRECTION', 'NO_CREDIT_CONFIRM', 'PERIOD_YEAR_ACCEPT', 'PERIOD_MONTH_CONFIRM')),
    CONSTRAINT fk_learning_credit_import_decision_batch FOREIGN KEY(batch_id) REFERENCES learning_credit_import_batches(id) ON DELETE CASCADE,
    CONSTRAINT fk_learning_credit_import_decision_user FOREIGN KEY(actor_user_id) REFERENCES app_users(id),
    CONSTRAINT uq_learning_credit_import_decision_key UNIQUE(idempotency_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_learning_credit_import_decision_batch
    ON learning_credit_import_decisions(batch_id, decision_type, target_type, target_id);

COMMIT;
