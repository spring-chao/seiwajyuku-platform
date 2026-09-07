-- 0049 rollback is intentionally loss-averse.  Once C7 facts or ledger rows
-- reference this model, restore a pre-0049 snapshot instead of dropping it.
BEGIN;
CREATE TEMP TABLE c7_learning_activity_rollback_guard (n INTEGER CHECK(n=0));
INSERT INTO c7_learning_activity_rollback_guard
SELECT COUNT(*) FROM learning_credit_activity_facts;
INSERT INTO c7_learning_activity_rollback_guard
SELECT COUNT(*) FROM learning_credit_entries
WHERE source_type IN ('LEARNING_ACTIVITY_DAILY_READING', 'LEARNING_ACTIVITY_EXCELLENT_SHARE');
DROP TABLE c7_learning_activity_rollback_guard;

DELETE FROM role_permissions
WHERE permission_key IN ('plans:business_calendar_manage', 'plans:credit_activity_fact_manage');
DELETE FROM permissions
WHERE permission_key IN ('plans:business_calendar_manage', 'plans:credit_activity_fact_manage');
DROP TABLE learning_credit_activity_facts;
DROP INDEX IF EXISTS idx_learning_calendar_days_lookup;
DROP TABLE learning_business_calendar_days;
DROP INDEX IF EXISTS idx_learning_calendar_versions_lookup;
DROP TABLE learning_business_calendar_versions;
DELETE FROM schema_migrations WHERE version='0049_c7_learning_activity_and_business_calendar.sql';
COMMIT;
