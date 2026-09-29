-- Loss-averse rollback: refuse if either table contains any business facts.
BEGIN;
CREATE TEMP TABLE credit_settlement_0067_rollback_guard (n INTEGER CHECK (n=0));
INSERT INTO credit_settlement_0067_rollback_guard SELECT COUNT(*) FROM learning_credit_settlement_batch_items;
INSERT INTO credit_settlement_0067_rollback_guard SELECT COUNT(*) FROM learning_credit_settlement_batches;
DROP TABLE credit_settlement_0067_rollback_guard;

DROP TABLE learning_credit_settlement_batch_items;
DROP TABLE learning_credit_settlement_batches;
DELETE FROM schema_migrations WHERE version='0067_learning_credit_settlement_batches.sql';
COMMIT;
