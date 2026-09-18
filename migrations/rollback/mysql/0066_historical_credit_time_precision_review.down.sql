-- 0066 rollback is loss-averse.  It refuses to remove period precision or
-- review evidence once either has been used.
START TRANSACTION;

CREATE TEMPORARY TABLE g5_4_c2_rollback_guard (n INT CHECK(n=0));
INSERT INTO g5_4_c2_rollback_guard
SELECT COUNT(*) FROM learning_credit_entries
WHERE occurred_precision <> 'EXACT_DATE' OR occurred_at IS NULL;
INSERT INTO g5_4_c2_rollback_guard SELECT COUNT(*) FROM learning_credit_import_items;
INSERT INTO g5_4_c2_rollback_guard SELECT COUNT(*) FROM learning_credit_import_class_mappings;
INSERT INTO g5_4_c2_rollback_guard SELECT COUNT(*) FROM learning_credit_import_decisions;
DROP TEMPORARY TABLE g5_4_c2_rollback_guard;

ALTER TABLE learning_credit_entries
    DROP INDEX idx_learning_credit_entries_member_period,
    DROP CHECK chk_learning_credit_entry_period,
    DROP CHECK chk_learning_credit_entry_month,
    DROP CHECK chk_learning_credit_entry_year,
    DROP CHECK chk_learning_credit_entry_precision,
    DROP COLUMN occurred_month,
    DROP COLUMN occurred_year,
    DROP COLUMN occurred_precision,
    MODIFY occurred_at DATETIME NOT NULL;

ALTER TABLE learning_credit_import_rows
    DROP FOREIGN KEY fk_learning_credit_import_row_credit_reviewer,
    DROP CHECK chk_learning_credit_import_row_credit_review,
    DROP COLUMN match_algorithm_version,
    DROP COLUMN match_snapshot_fingerprint,
    DROP COLUMN match_snapshot_id,
    DROP COLUMN credit_reviewed_at,
    DROP COLUMN credit_reviewed_by,
    DROP COLUMN credit_review_reason,
    DROP COLUMN credit_review_status,
    DROP COLUMN resolved_total_points;

ALTER TABLE learning_credit_import_items
    DROP CHECK chk_learning_credit_import_item_period_status,
    DROP CHECK chk_learning_credit_import_item_period_month,
    DROP CHECK chk_learning_credit_import_item_year,
    DROP CHECK chk_learning_credit_import_item_precision,
    DROP COLUMN period_review_status,
    DROP COLUMN occurred_month,
    DROP COLUMN occurred_year,
    DROP COLUMN occurred_precision;

DROP TABLE learning_credit_import_decisions;
DROP TABLE learning_credit_import_class_mappings;
DELETE FROM schema_migrations WHERE version='0066_historical_credit_time_precision_review.sql';
COMMIT;
