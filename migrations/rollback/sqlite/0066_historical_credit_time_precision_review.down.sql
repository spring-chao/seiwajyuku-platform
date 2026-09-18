-- 0066 rollback is loss-averse.  A non-exact ledger period or any C2B/C2C
-- evidence blocks rollback because removing the new columns would destroy
-- the audit meaning of the staging data.
BEGIN;

CREATE TEMP TABLE g5_4_c2_non_exact_guard (n INTEGER);
CREATE TEMP TRIGGER g5_4_c2_non_exact_abort
BEFORE INSERT ON g5_4_c2_non_exact_guard
WHEN NEW.n <> 0
BEGIN
    SELECT RAISE(ABORT, 'ROLLBACK_BLOCKED_BY_NON_EXACT_PERIOD');
END;
INSERT INTO g5_4_c2_non_exact_guard
SELECT COUNT(*) FROM learning_credit_entries
WHERE occurred_precision <> 'EXACT_DATE' OR occurred_at IS NULL;
DROP TRIGGER g5_4_c2_non_exact_abort;
DROP TABLE g5_4_c2_non_exact_guard;

CREATE TEMP TABLE g5_4_c2_evidence_guard (n INTEGER);
CREATE TEMP TRIGGER g5_4_c2_evidence_abort
BEFORE INSERT ON g5_4_c2_evidence_guard
WHEN NEW.n <> 0
BEGIN
    SELECT RAISE(ABORT, 'ROLLBACK_BLOCKED_BY_C2B_C2C_EVIDENCE');
END;
INSERT INTO g5_4_c2_evidence_guard SELECT COUNT(*) FROM learning_credit_import_items;
INSERT INTO g5_4_c2_evidence_guard SELECT COUNT(*) FROM learning_credit_import_class_mappings;
INSERT INTO g5_4_c2_evidence_guard SELECT COUNT(*) FROM learning_credit_import_decisions;
DROP TRIGGER g5_4_c2_evidence_abort;
DROP TABLE g5_4_c2_evidence_guard;

DROP INDEX IF EXISTS idx_learning_credit_entries_member_time;
DROP INDEX IF EXISTS idx_learning_credit_entries_member_category;
DROP INDEX IF EXISTS idx_learning_credit_entries_source;
DROP INDEX IF EXISTS idx_learning_credit_entries_cycle;

ALTER TABLE learning_credit_entries RENAME TO learning_credit_entries__0066_new;
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
INSERT INTO learning_credit_entries (
    id, member_id, credit_category, credit_type, points, source_type, source_id,
    class_org_unit_id, learning_cycle_id, rule_key, rule_version, rule_version_id,
    rule_snapshot_json, occurred_at, posted_at, status, idempotency_key,
    reversal_of_entry_id, created_by, created_at, updated_at
)
SELECT id, member_id, credit_category, credit_type, points, source_type, source_id,
       class_org_unit_id, learning_cycle_id, rule_key, rule_version, rule_version_id,
       rule_snapshot_json, occurred_at, posted_at, status, idempotency_key,
       reversal_of_entry_id, created_by, created_at, updated_at
FROM learning_credit_entries__0066_new;
CREATE INDEX idx_learning_credit_entries_member_time
    ON learning_credit_entries(member_id, occurred_at, id);
CREATE INDEX idx_learning_credit_entries_member_category
    ON learning_credit_entries(member_id, credit_category, status);
CREATE INDEX idx_learning_credit_entries_source
    ON learning_credit_entries(source_type, source_id);
CREATE INDEX idx_learning_credit_entries_cycle
    ON learning_credit_entries(learning_cycle_id, credit_type, status);
DROP TABLE learning_credit_entries__0066_new;

DROP TABLE learning_credit_import_decisions;
DROP TABLE learning_credit_import_class_mappings;

DROP INDEX IF EXISTS idx_learning_credit_import_item_dry_run;
ALTER TABLE learning_credit_import_items RENAME TO learning_credit_import_items__0066_new;
CREATE TABLE learning_credit_import_items (
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
INSERT INTO learning_credit_import_items (
    id, batch_id, import_row_id, matched_member_id, source_sheet, source_row_number,
    source_column_index, source_column_name, credit_category, legacy_credit_type,
    source_month, accounting_month, points, source_rule_version, status,
    metadata_json, created_at, updated_at
)
SELECT id, batch_id, import_row_id, matched_member_id, source_sheet, source_row_number,
       source_column_index, source_column_name, credit_category, legacy_credit_type,
       source_month, accounting_month, points, source_rule_version, status,
       metadata_json, created_at, updated_at
FROM learning_credit_import_items__0066_new;
CREATE INDEX idx_learning_credit_import_item_dry_run
    ON learning_credit_import_items(batch_id, matched_member_id, source_month, status, id);
DROP TABLE learning_credit_import_items__0066_new;

DROP INDEX IF EXISTS idx_learning_credit_import_row_review;
ALTER TABLE learning_credit_import_rows RENAME TO learning_credit_import_rows__0066_new;
CREATE TABLE learning_credit_import_rows (
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
INSERT INTO learning_credit_import_rows (
    id, batch_id, source_sheet, source_row_number, raw_name, raw_class_name,
    raw_group_name, raw_total_points, calculated_total_points, matched_member_id,
    match_status, validation_status, review_status, match_reason, metadata_json,
    created_at, updated_at
)
SELECT id, batch_id, source_sheet, source_row_number, raw_name, raw_class_name,
       raw_group_name, raw_total_points, calculated_total_points, matched_member_id,
       match_status, validation_status, review_status, match_reason, metadata_json,
       created_at, updated_at
FROM learning_credit_import_rows__0066_new;
CREATE INDEX idx_learning_credit_import_row_review
    ON learning_credit_import_rows(batch_id, validation_status, review_status, id);
DROP TABLE learning_credit_import_rows__0066_new;

DELETE FROM schema_migrations WHERE version='0066_historical_credit_time_precision_review.sql';
COMMIT;
