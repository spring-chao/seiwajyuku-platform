-- 0049 rollback is intentionally loss-averse.  Restore a pre-0049 snapshot
-- after facts or ledger rows have been created instead of dropping data.
CREATE TEMPORARY TABLE c7_learning_activity_rollback_guard (n INT CHECK(n=0));
INSERT INTO c7_learning_activity_rollback_guard
SELECT COUNT(*) FROM learning_credit_activity_facts;
INSERT INTO c7_learning_activity_rollback_guard
SELECT COUNT(*) FROM learning_credit_entries
WHERE source_type IN ('LEARNING_ACTIVITY_DAILY_READING', 'LEARNING_ACTIVITY_EXCELLENT_SHARE');
DROP TEMPORARY TABLE c7_learning_activity_rollback_guard;

DELETE FROM role_permissions
WHERE permission_key IN ('plans:business_calendar_manage', 'plans:credit_activity_fact_manage');
DELETE FROM permissions
WHERE permission_key IN ('plans:business_calendar_manage', 'plans:credit_activity_fact_manage');
DROP TABLE learning_credit_activity_facts;
DROP TABLE learning_business_calendar_days;
DROP TABLE learning_business_calendar_versions;
DELETE FROM schema_migrations WHERE version='0049_c7_learning_activity_and_business_calendar.sql';
