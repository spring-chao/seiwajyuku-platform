-- Empty-schema reversal only. Retain every uploaded source and batch link.
CREATE TEMPORARY TABLE credit_source_rollback_guard(ok INT NOT NULL CHECK(ok=1));
INSERT INTO credit_source_rollback_guard(ok)
SELECT CASE WHEN (SELECT COUNT(*) FROM learning_credit_source_imports)=0
 AND (SELECT COUNT(*) FROM learning_credit_source_rows)=0
 AND (SELECT COUNT(*) FROM learning_credit_source_batches)=0 THEN 1 ELSE 0 END;
DROP TABLE learning_credit_source_batches;
DROP TABLE learning_credit_source_rows;
DROP TABLE learning_credit_source_imports;
DELETE FROM schema_migrations WHERE version='0072_learning_credit_source_imports.sql';
DROP TABLE credit_source_rollback_guard;
