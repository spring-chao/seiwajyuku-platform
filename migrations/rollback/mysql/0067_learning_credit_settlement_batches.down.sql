-- Loss-averse rollback: refuse if either table contains any business facts.
CREATE TEMPORARY TABLE credit_settlement_0067_rollback_guard (n INT CHECK (n=0));
INSERT INTO credit_settlement_0067_rollback_guard SELECT COUNT(*) FROM information_schema.tables WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('learning_credit_source_batches','learning_credit_source_rows','learning_credit_source_imports');
INSERT INTO credit_settlement_0067_rollback_guard SELECT COUNT(*) FROM learning_credit_settlement_batch_items;
INSERT INTO credit_settlement_0067_rollback_guard SELECT COUNT(*) FROM learning_credit_settlement_batches;
INSERT INTO credit_settlement_0067_rollback_guard
SELECT COUNT(*) FROM role_permissions
WHERE permission_key IN ('plans:credit_settlement_approve', 'plans:credit_settlement_post');
DROP TEMPORARY TABLE credit_settlement_0067_rollback_guard;

DROP TABLE learning_credit_settlement_batch_items;
DROP TABLE learning_credit_settlement_batches;
DELETE FROM permissions
WHERE permission_key IN ('plans:credit_settlement_approve', 'plans:credit_settlement_post');
DELETE FROM schema_migrations WHERE version='0067_learning_credit_settlement_batches.sql';
