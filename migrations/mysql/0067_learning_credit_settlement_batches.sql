-- 0067: Unified learning-credit settlement batch envelope.
-- This only creates empty orchestration tables. It never posts ledger entries.
-- Production execution remains separately gated after 0064/0065/0066.

CREATE TABLE IF NOT EXISTS learning_credit_settlement_batches (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    batch_no VARCHAR(64) NOT NULL,
    batch_type VARCHAR(32) NOT NULL,
    source_type VARCHAR(64) NOT NULL,
    class_org_unit_id VARCHAR(64) NULL,
    period_precision VARCHAR(16) NOT NULL,
    period_start DATE NULL,
    period_end DATE NULL,
    period_year INT NULL,
    period_month INT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    proposed_entry_count INT NOT NULL DEFAULT 0,
    proposed_points DECIMAL(12,2) NOT NULL DEFAULT 0,
    blocked_count INT NOT NULL DEFAULT 0,
    approved_by BIGINT NULL,
    approved_at DATETIME NULL,
    posted_entry_count INT NOT NULL DEFAULT 0,
    posted_points DECIMAL(12,2) NOT NULL DEFAULT 0,
    source_snapshot_json TEXT NOT NULL,
    rule_snapshot_json TEXT NOT NULL,
    result_snapshot_json TEXT NOT NULL,
    source_fingerprint CHAR(64) NOT NULL,
    rule_fingerprint CHAR(64) NOT NULL,
    approval_fingerprint CHAR(64) NULL,
    created_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_credit_settlement_batch_no UNIQUE (batch_no),
    CONSTRAINT uq_credit_settlement_source UNIQUE (source_type, source_fingerprint, batch_type),
    CONSTRAINT chk_credit_settlement_batch_type CHECK (batch_type IN ('REGULAR', 'HISTORICAL_IMPORT', 'CORRECTION')),
    CONSTRAINT chk_credit_settlement_batch_status CHECK (status IN ('DRAFT', 'DRY_RUN', 'PENDING_APPROVAL', 'APPROVED', 'POSTING', 'POSTED', 'PARTIAL_FAILED', 'CLOSED', 'CANCELLED')),
    CONSTRAINT chk_credit_settlement_batch_counts CHECK (proposed_entry_count >= 0 AND blocked_count >= 0 AND posted_entry_count >= 0),
    CONSTRAINT chk_credit_settlement_batch_period CHECK (
        (period_precision='EXACT_DATE' AND period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end AND period_year IS NULL AND period_month IS NULL)
        OR (period_precision='MONTH' AND period_start IS NULL AND period_end IS NULL AND period_year IS NOT NULL AND period_year BETWEEN 2000 AND 2100 AND period_month IS NOT NULL AND period_month BETWEEN 1 AND 12)
        OR (period_precision='YEAR' AND period_start IS NULL AND period_end IS NULL AND period_year IS NOT NULL AND period_year BETWEEN 2000 AND 2100 AND period_month IS NULL)
    ),
    CONSTRAINT fk_credit_settlement_batch_class FOREIGN KEY (class_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_credit_settlement_batch_approver FOREIGN KEY (approved_by) REFERENCES app_users(id),
    CONSTRAINT fk_credit_settlement_batch_creator FOREIGN KEY (created_by) REFERENCES app_users(id),
    INDEX idx_credit_settlement_batch_status (status, source_type, id),
    INDEX idx_credit_settlement_batch_class (class_org_unit_id, status, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS learning_credit_settlement_batch_items (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    batch_id BIGINT NOT NULL,
    member_id BIGINT NULL,
    source_type VARCHAR(64) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    source_snapshot_json TEXT NOT NULL,
    rule_key VARCHAR(128) NULL,
    rule_version VARCHAR(64) NULL,
    rule_version_id BIGINT NULL,
    rule_snapshot_json TEXT NOT NULL,
    credit_category VARCHAR(32) NULL,
    credit_type VARCHAR(64) NULL,
    points DECIMAL(10,2) NULL,
    occurred_at DATETIME NULL,
    occurred_precision VARCHAR(16) NOT NULL,
    occurred_year INT NOT NULL,
    occurred_month INT NULL,
    idempotency_key VARCHAR(255) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PROPOSED',
    blocking_reason VARCHAR(128) NULL,
    error_code VARCHAR(128) NULL,
    ledger_entry_id BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_credit_settlement_batch_item_key UNIQUE (batch_id, idempotency_key),
    CONSTRAINT chk_credit_settlement_item_status CHECK (status IN ('PROPOSED', 'BLOCKED', 'APPROVED', 'POSTING', 'POSTED', 'FAILED', 'CANCELLED', 'REVERSED')),
    CONSTRAINT chk_credit_settlement_item_category CHECK (credit_category IS NULL OR credit_category IN ('STANDARD_LEARNING', 'EXTENSION_ACTIVITY')),
    CONSTRAINT chk_credit_settlement_item_period CHECK (
        occurred_year BETWEEN 2000 AND 2100 AND
        ((occurred_precision='EXACT_DATE' AND occurred_at IS NOT NULL AND occurred_month IS NOT NULL AND occurred_month BETWEEN 1 AND 12)
         OR (occurred_precision='MONTH' AND occurred_at IS NULL AND occurred_month IS NOT NULL AND occurred_month BETWEEN 1 AND 12)
         OR (occurred_precision='YEAR' AND occurred_at IS NULL AND occurred_month IS NULL))
    ),
    CONSTRAINT chk_credit_settlement_item_posted CHECK (status <> 'POSTED' OR (ledger_entry_id IS NOT NULL AND member_id IS NOT NULL AND idempotency_key IS NOT NULL)),
    CONSTRAINT fk_credit_settlement_item_batch FOREIGN KEY (batch_id) REFERENCES learning_credit_settlement_batches(id),
    CONSTRAINT fk_credit_settlement_item_member FOREIGN KEY (member_id) REFERENCES members(id),
    CONSTRAINT fk_credit_settlement_item_ledger FOREIGN KEY (ledger_entry_id) REFERENCES learning_credit_entries(id),
    INDEX idx_credit_settlement_item_status (batch_id, status, id),
    INDEX idx_credit_settlement_item_source (source_type, source_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Catalog only: reviewers and posters are not granted to any role here.
INSERT IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('plans:credit_settlement_approve', '审批学分结算批次', 'SENSITIVE', UTC_TIMESTAMP());
INSERT IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('plans:credit_settlement_post', '按已审批批次正式入账学分', 'SENSITIVE', UTC_TIMESTAMP());
