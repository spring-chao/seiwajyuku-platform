-- 0066: G5.4 C2B/C2C historical review workbench and period precision.
--
-- This migration represents an incomplete historical period explicitly.  It
-- never invents a month/day and it does not post anything to the ledger.
BEGIN;

-- The ledger used to require occurred_at.  Rebuild it so MONTH and YEAR
-- entries can remain auditable without a fabricated date.
CREATE TEMP TABLE g5_4_c2_ledger_source_guard (n INTEGER CHECK(n=0));
INSERT INTO g5_4_c2_ledger_source_guard
SELECT COUNT(*)
FROM learning_credit_entries
WHERE occurred_at IS NULL
   OR length(substr(occurred_at, 1, 10)) <> 10
   OR substr(occurred_at, 5, 1) <> '-'
   OR substr(occurred_at, 8, 1) <> '-'
   OR CAST(substr(occurred_at, 1, 4) AS INTEGER) NOT BETWEEN 2000 AND 2100
   OR CAST(substr(occurred_at, 6, 2) AS INTEGER) NOT BETWEEN 1 AND 12;
DROP TABLE g5_4_c2_ledger_source_guard;

DROP INDEX IF EXISTS idx_learning_credit_entries_member_time;
DROP INDEX IF EXISTS idx_learning_credit_entries_member_category;
DROP INDEX IF EXISTS idx_learning_credit_entries_source;
DROP INDEX IF EXISTS idx_learning_credit_entries_cycle;
DROP INDEX IF EXISTS idx_learning_credit_entries_member_period;

ALTER TABLE learning_credit_entries RENAME TO learning_credit_entries__0066_old;

CREATE TABLE learning_credit_entries (
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
    occurred_at TEXT,
    occurred_precision TEXT NOT NULL DEFAULT 'EXACT_DATE'
        CHECK(occurred_precision IN ('EXACT_DATE', 'MONTH', 'YEAR')),
    occurred_year INTEGER NOT NULL CHECK(occurred_year BETWEEN 2000 AND 2100),
    occurred_month INTEGER CHECK(occurred_month IS NULL OR occurred_month BETWEEN 1 AND 12),
    posted_at TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(status IN ('PENDING', 'POSTED', 'REVERSED', 'VOID')),
    idempotency_key TEXT NOT NULL UNIQUE,
    reversal_of_entry_id INTEGER REFERENCES learning_credit_entries(id),
    created_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(
        (occurred_precision='EXACT_DATE' AND occurred_at IS NOT NULL AND occurred_month IS NOT NULL)
        OR (occurred_precision='MONTH' AND occurred_at IS NULL AND occurred_month IS NOT NULL)
        OR (occurred_precision='YEAR' AND occurred_at IS NULL AND occurred_month IS NULL)
    )
);

INSERT INTO learning_credit_entries (
    id, member_id, credit_category, credit_type, points, source_type, source_id,
    class_org_unit_id, learning_cycle_id, rule_key, rule_version, rule_version_id,
    rule_snapshot_json, occurred_at, occurred_precision, occurred_year, occurred_month,
    posted_at, status, idempotency_key, reversal_of_entry_id, created_by, created_at, updated_at
)
SELECT
    id, member_id, credit_category, credit_type, points, source_type, source_id,
    class_org_unit_id, learning_cycle_id, rule_key, rule_version, rule_version_id,
    rule_snapshot_json, occurred_at, 'EXACT_DATE', CAST(substr(occurred_at, 1, 4) AS INTEGER),
    CAST(substr(occurred_at, 6, 2) AS INTEGER), posted_at, status, idempotency_key,
    reversal_of_entry_id, created_by, created_at, updated_at
FROM learning_credit_entries__0066_old;

CREATE INDEX idx_learning_credit_entries_member_time
    ON learning_credit_entries(member_id, occurred_year, occurred_month, occurred_at, id);
CREATE INDEX idx_learning_credit_entries_member_category
    ON learning_credit_entries(member_id, credit_category, status);
CREATE INDEX idx_learning_credit_entries_source
    ON learning_credit_entries(source_type, source_id);
CREATE INDEX idx_learning_credit_entries_cycle
    ON learning_credit_entries(learning_cycle_id, credit_type, status);

CREATE TEMP TABLE g5_4_c2_ledger_copy_guard (n INTEGER CHECK(n=0));
INSERT INTO g5_4_c2_ledger_copy_guard
SELECT (SELECT COUNT(*) FROM learning_credit_entries)
     - (SELECT COUNT(*) FROM learning_credit_entries__0066_old);
INSERT INTO g5_4_c2_ledger_copy_guard
SELECT COALESCE((SELECT SUM(points) FROM learning_credit_entries), 0)
     - COALESCE((SELECT SUM(points) FROM learning_credit_entries__0066_old), 0);
INSERT INTO g5_4_c2_ledger_copy_guard
SELECT COUNT(*) FROM learning_credit_entries
WHERE occurred_precision <> 'EXACT_DATE' OR occurred_at IS NULL;
DROP TABLE g5_4_c2_ledger_copy_guard;
DROP TABLE learning_credit_entries__0066_old;

ALTER TABLE learning_credit_import_rows ADD COLUMN resolved_total_points REAL;
ALTER TABLE learning_credit_import_rows ADD COLUMN credit_review_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED'
    CHECK(credit_review_status IN ('NOT_REQUIRED', 'PENDING', 'APPROVED', 'NO_CREDIT_CONFIRMED', 'REJECTED', 'NEEDS_SOURCE_CORRECTION'));
ALTER TABLE learning_credit_import_rows ADD COLUMN credit_review_reason TEXT;
ALTER TABLE learning_credit_import_rows ADD COLUMN credit_reviewed_by INTEGER REFERENCES app_users(id);
ALTER TABLE learning_credit_import_rows ADD COLUMN credit_reviewed_at TEXT;
ALTER TABLE learning_credit_import_rows ADD COLUMN match_snapshot_id TEXT;
ALTER TABLE learning_credit_import_rows ADD COLUMN match_snapshot_fingerprint TEXT;
ALTER TABLE learning_credit_import_rows ADD COLUMN match_algorithm_version TEXT;
UPDATE learning_credit_import_rows
SET credit_review_status=CASE
    WHEN validation_status='PASS' THEN 'NOT_REQUIRED'
    ELSE 'PENDING'
END;

ALTER TABLE learning_credit_import_items ADD COLUMN occurred_precision TEXT NOT NULL DEFAULT 'YEAR'
    CHECK(occurred_precision IN ('EXACT_DATE', 'MONTH', 'YEAR'));
ALTER TABLE learning_credit_import_items ADD COLUMN occurred_year INTEGER NOT NULL DEFAULT 2026
    CHECK(occurred_year BETWEEN 2000 AND 2100);
ALTER TABLE learning_credit_import_items ADD COLUMN occurred_month INTEGER
    CHECK(occurred_month IS NULL OR occurred_month BETWEEN 1 AND 12);
ALTER TABLE learning_credit_import_items ADD COLUMN period_review_status TEXT NOT NULL DEFAULT 'PERIOD_REVIEW_REQUIRED'
    CHECK(period_review_status IN ('READY', 'MONTH_CONFIRMED', 'YEAR_ACCEPTED', 'PERIOD_REVIEW_REQUIRED', 'DUAL_TRACK_PENDING', 'REJECTED'));

UPDATE learning_credit_import_items
SET occurred_year=(SELECT source_year FROM learning_credit_import_batches b WHERE b.id=learning_credit_import_items.batch_id),
    occurred_month=source_month,
    occurred_precision=CASE WHEN source_month IS NULL THEN 'YEAR' ELSE 'MONTH' END,
    period_review_status=CASE
        WHEN source_month=9 THEN 'DUAL_TRACK_PENDING'
        WHEN source_month BETWEEN 1 AND 8 THEN 'READY'
        ELSE 'PERIOD_REVIEW_REQUIRED'
    END;

CREATE TABLE IF NOT EXISTS learning_credit_import_class_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES learning_credit_import_batches(id) ON DELETE CASCADE,
    source_sheet TEXT NOT NULL,
    raw_class_name TEXT,
    candidate_org_unit_ids_json TEXT NOT NULL DEFAULT '[]',
    confirmed_org_unit_id TEXT,
    mapping_status TEXT NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK(mapping_status IN ('PENDING_REVIEW', 'EXACT', 'AUTO_RESOLVED', 'CONFIRMED_ALIAS', 'AMBIGUOUS', 'NOT_FOUND', 'REJECTED')),
    mapping_reason TEXT NOT NULL,
    snapshot_id TEXT,
    snapshot_fingerprint TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    confirmed_by INTEGER REFERENCES app_users(id),
    confirmed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(batch_id, source_sheet, raw_class_name)
);
CREATE INDEX IF NOT EXISTS idx_learning_credit_import_class_mapping_review
    ON learning_credit_import_class_mappings(batch_id, mapping_status, source_sheet, raw_class_name);

CREATE TABLE IF NOT EXISTS learning_credit_import_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES learning_credit_import_batches(id) ON DELETE CASCADE,
    decision_type TEXT NOT NULL
        CHECK(decision_type IN (
            'CLASS_MAPPING_CONFIRM', 'MEMBER_MATCH_CONFIRM', 'AUTO_MATCH_BULK_CONFIRM',
            'CREDIT_TOTAL_APPROVE', 'CREDIT_TOTAL_REJECT', 'CREDIT_SOURCE_CORRECTION',
            'NO_CREDIT_CONFIRM', 'PERIOD_YEAR_ACCEPT',
            'PERIOD_MONTH_CONFIRM'
        )),
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    snapshot_id TEXT,
    snapshot_fingerprint TEXT,
    algorithm_version TEXT,
    actor_user_id INTEGER REFERENCES app_users(id),
    reason TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_credit_import_decision_batch
    ON learning_credit_import_decisions(batch_id, decision_type, target_type, target_id);

COMMIT;
