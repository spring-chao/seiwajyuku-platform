-- Refuse downgrade after any source/review/post has been retained.
CREATE TEMPORARY TABLE credit_opening_rollback_guard (ok INT NOT NULL CHECK(ok=1));
INSERT INTO credit_opening_rollback_guard(ok)
SELECT CASE WHEN (SELECT COUNT(*) FROM learning_credit_opening_imports)=0
 AND (SELECT COUNT(*) FROM learning_credit_opening_rows)=0
 AND (SELECT COUNT(*) FROM learning_credit_opening_balances)=0
 AND NOT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name IN ('learning_credit_year_imports','learning_credit_year_rows','learning_credit_year_allocations','learning_credit_class_progress')) THEN 1 ELSE 0 END;
DELETE FROM role_permissions WHERE permission_key='plans:credit_opening_manage' AND role_key IN ('employee_learning_management','ops_center_learning');
DELETE FROM permissions WHERE permission_key='plans:credit_opening_manage' AND NOT EXISTS (SELECT 1 FROM role_permissions WHERE permission_key='plans:credit_opening_manage');
DROP TABLE learning_credit_opening_balances;
DROP TABLE learning_credit_opening_rows;
DROP TABLE learning_credit_opening_imports;
DELETE FROM schema_migrations WHERE version='0069_learning_credit_opening_balances.sql';
DROP TABLE credit_opening_rollback_guard;
