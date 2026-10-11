-- Empty-schema reversal only. Keep any retained source, review or allocation.
CREATE TEMPORARY TABLE credit_year_rollback_guard(ok INT NOT NULL CHECK(ok=1));
INSERT INTO credit_year_rollback_guard(ok)
SELECT CASE WHEN (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name IN ('learning_credit_year_reconciliation_rows','learning_credit_opening_adjustments'))=0 THEN 1 ELSE 0 END;
INSERT INTO credit_year_rollback_guard(ok)
SELECT CASE WHEN (SELECT COUNT(*) FROM learning_credit_year_imports)=0
 AND (SELECT COUNT(*) FROM learning_credit_year_rows)=0
 AND (SELECT COUNT(*) FROM learning_credit_year_allocations)=0
 AND (SELECT COUNT(*) FROM learning_credit_class_progress)=0 THEN 1 ELSE 0 END;
DROP TABLE learning_credit_class_progress;
DROP TABLE learning_credit_year_allocations;
DROP TABLE learning_credit_year_rows;
DROP TABLE learning_credit_year_imports;
DELETE FROM schema_migrations WHERE version='0070_learning_credit_year_allocations.sql';
DROP TABLE credit_year_rollback_guard;
