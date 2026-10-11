CREATE TEMPORARY TABLE credit_reconcile_rollback_guard(ok INT NOT NULL CHECK(ok=1));
INSERT INTO credit_reconcile_rollback_guard(ok) SELECT CASE WHEN (SELECT COUNT(*) FROM learning_credit_year_reconciliation_rows)=0 AND (SELECT COUNT(*) FROM learning_credit_opening_adjustments)=0 THEN 1 ELSE 0 END;
DROP TABLE learning_credit_opening_adjustments;
DROP TABLE learning_credit_year_reconciliation_rows;
DELETE FROM schema_migrations WHERE version='0071_learning_credit_year_reconciliation.sql';
DROP TABLE credit_reconcile_rollback_guard;
