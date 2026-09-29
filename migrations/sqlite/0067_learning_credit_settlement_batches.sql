-- 0067: Unified learning-credit settlement batch envelope; no ledger posting.
BEGIN;

CREATE TABLE IF NOT EXISTS learning_credit_settlement_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no TEXT NOT NULL UNIQUE,
    batch_type TEXT NOT NULL CHECK (batch_type IN ('REGULAR', 'HISTORICAL_IMPORT', 'CORRECTION')),
    source_type TEXT NOT NULL,
    class_org_unit_id TEXT REFERENCES org_units(id),
    period_precision TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    period_year INTEGER,
    period_month INTEGER,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'DRY_RUN', 'PENDING_APPROVAL', 'APPROVED', 'POSTING', 'POSTED', 'PARTIAL_FAILED', 'CLOSED', 'CANCELLED')),
    proposed_entry_count INTEGER NOT NULL DEFAULT 0 CHECK (proposed_entry_count >= 0),
    proposed_points REAL NOT NULL DEFAULT 0,
    blocked_count INTEGER NOT NULL DEFAULT 0 CHECK (blocked_count >= 0),
    approved_by INTEGER REFERENCES app_users(id),
    approved_at TEXT,
    posted_entry_count INTEGER NOT NULL DEFAULT 0 CHECK (posted_entry_count >= 0),
    posted_points REAL NOT NULL DEFAULT 0,
    source_snapshot_json TEXT NOT NULL,
    rule_snapshot_json TEXT NOT NULL,
    result_snapshot_json TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    rule_fingerprint TEXT NOT NULL,
    approval_fingerprint TEXT,
    created_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source_type, source_fingerprint, batch_type),
    CHECK (
        (period_precision='EXACT_DATE' AND period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end AND period_year IS NULL AND period_month IS NULL)
        OR (period_precision='MONTH' AND period_start IS NULL AND period_end IS NULL AND period_year IS NOT NULL AND period_year BETWEEN 2000 AND 2100 AND period_month IS NOT NULL AND period_month BETWEEN 1 AND 12)
        OR (period_precision='YEAR' AND period_start IS NULL AND period_end IS NULL AND period_year IS NOT NULL AND period_year BETWEEN 2000 AND 2100 AND period_month IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_credit_settlement_batch_status ON learning_credit_settlement_batches(status, source_type, id);
CREATE INDEX IF NOT EXISTS idx_credit_settlement_batch_class ON learning_credit_settlement_batches(class_org_unit_id, status, id);

CREATE TABLE IF NOT EXISTS learning_credit_settlement_batch_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES learning_credit_settlement_batches(id),
    member_id INTEGER REFERENCES members(id),
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_snapshot_json TEXT NOT NULL,
    rule_key TEXT,
    rule_version TEXT,
    rule_version_id INTEGER,
    rule_snapshot_json TEXT NOT NULL,
    credit_category TEXT CHECK (credit_category IS NULL OR credit_category IN ('STANDARD_LEARNING', 'EXTENSION_ACTIVITY')),
    credit_type TEXT,
    points REAL,
    occurred_at TEXT,
    occurred_precision TEXT NOT NULL,
    occurred_year INTEGER NOT NULL CHECK (occurred_year BETWEEN 2000 AND 2100),
    occurred_month INTEGER CHECK (occurred_month IS NULL OR occurred_month BETWEEN 1 AND 12),
    idempotency_key TEXT,
    status TEXT NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED', 'BLOCKED', 'APPROVED', 'POSTING', 'POSTED', 'FAILED', 'CANCELLED', 'REVERSED')),
    blocking_reason TEXT,
    error_code TEXT,
    ledger_entry_id INTEGER REFERENCES learning_credit_entries(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (batch_id, idempotency_key),
    CHECK (
        (occurred_precision='EXACT_DATE' AND occurred_at IS NOT NULL AND occurred_month IS NOT NULL AND occurred_month BETWEEN 1 AND 12)
        OR (occurred_precision='MONTH' AND occurred_at IS NULL AND occurred_month IS NOT NULL AND occurred_month BETWEEN 1 AND 12)
        OR (occurred_precision='YEAR' AND occurred_at IS NULL AND occurred_month IS NULL)
    ),
    CHECK (status <> 'POSTED' OR (ledger_entry_id IS NOT NULL AND member_id IS NOT NULL AND idempotency_key IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_credit_settlement_item_status ON learning_credit_settlement_batch_items(batch_id, status, id);
CREATE INDEX IF NOT EXISTS idx_credit_settlement_item_source ON learning_credit_settlement_batch_items(source_type, source_id);

COMMIT;
