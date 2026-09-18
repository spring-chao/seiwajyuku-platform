-- 0065 rollback is loss-averse.  Import evidence must be explicitly archived
-- or restored before the staging schema is removed.
CREATE TEMPORARY TABLE g5_4_c1_rollback_guard (n INT CHECK(n=0));
INSERT INTO g5_4_c1_rollback_guard SELECT COUNT(*) FROM learning_credit_import_batches;
INSERT INTO g5_4_c1_rollback_guard SELECT COUNT(*) FROM learning_credit_import_rows;
INSERT INTO g5_4_c1_rollback_guard SELECT COUNT(*) FROM learning_credit_import_items;
INSERT INTO g5_4_c1_rollback_guard
SELECT COUNT(*) FROM learning_credit_entries
WHERE source_type='HISTORICAL_CREDIT_IMPORT';
DROP TEMPORARY TABLE g5_4_c1_rollback_guard;

DELETE FROM role_permissions
WHERE permission_key='plans:historical_credit_import_manage';
DELETE FROM permissions
WHERE permission_key='plans:historical_credit_import_manage';
DROP TABLE learning_credit_import_items;
DROP TABLE learning_credit_import_rows;
DROP TABLE learning_credit_import_batches;
DELETE FROM schema_migrations WHERE version='0065_learning_credit_history_import.sql';
