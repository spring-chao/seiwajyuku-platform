-- 0050 rollback is loss-averse.  HQ observations, source facts, or ledger rows
-- must be removed/restored through an approved data procedure before schema rollback.
BEGIN;
CREATE TEMP TABLE hq_reading_import_rollback_guard (n INTEGER CHECK(n=0));
INSERT INTO hq_reading_import_rollback_guard
SELECT COUNT(*) FROM hq_reading_import_observations;
INSERT INTO hq_reading_import_rollback_guard
SELECT COUNT(*) FROM learning_credit_activity_facts
WHERE source_type='HQ_READING_EXPORT';
INSERT INTO hq_reading_import_rollback_guard
SELECT COUNT(*) FROM learning_credit_entries
WHERE source_type='HQ_READING_EXPORT';
DROP TABLE hq_reading_import_rollback_guard;

DELETE FROM role_permissions
WHERE permission_key='plans:hq_reading_import_manage';
DELETE FROM permissions
WHERE permission_key='plans:hq_reading_import_manage';
DROP INDEX IF EXISTS idx_hq_reading_observations_identity;
DROP INDEX IF EXISTS idx_hq_reading_observations_class_date;
DROP INDEX IF EXISTS idx_hq_reading_observations_batch;
DROP TABLE hq_reading_import_observations;
DROP INDEX IF EXISTS idx_hq_reading_source_identity_member;
DROP TABLE hq_reading_source_identities;
DROP INDEX IF EXISTS idx_hq_reading_batches_class_date;
DROP TABLE hq_reading_import_batches;
DELETE FROM schema_migrations WHERE version='0050_c7_hq_reading_import.sql';
COMMIT;
