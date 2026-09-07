-- 0050 rollback is loss-averse.  Restore a pre-0050 snapshot after HQ
-- observations, source facts, or ledger rows have been created.
CREATE TEMPORARY TABLE hq_reading_import_rollback_guard (n INT CHECK(n=0));
INSERT INTO hq_reading_import_rollback_guard
SELECT COUNT(*) FROM hq_reading_import_observations;
INSERT INTO hq_reading_import_rollback_guard
SELECT COUNT(*) FROM learning_credit_activity_facts
WHERE source_type='HQ_READING_EXPORT';
INSERT INTO hq_reading_import_rollback_guard
SELECT COUNT(*) FROM learning_credit_entries
WHERE source_type='HQ_READING_EXPORT';
DROP TEMPORARY TABLE hq_reading_import_rollback_guard;

DELETE FROM role_permissions
WHERE permission_key='plans:hq_reading_import_manage';
DELETE FROM permissions
WHERE permission_key='plans:hq_reading_import_manage';
DROP TABLE hq_reading_import_observations;
DROP TABLE hq_reading_source_identities;
DROP TABLE hq_reading_import_batches;
DELETE FROM schema_migrations WHERE version='0050_c7_hq_reading_import.sql';
