CREATE TEMPORARY TABLE credit_posting_job_rollback_guard(ok INTEGER NOT NULL CHECK(ok=1));
INSERT INTO credit_posting_job_rollback_guard(ok)
SELECT CASE WHEN (SELECT COUNT(*) FROM learning_credit_posting_jobs)=0 THEN 1 ELSE 0 END;
DROP TABLE learning_credit_posting_jobs;
DELETE FROM schema_migrations WHERE version='0074_credit_posting_jobs.sql';
DROP TABLE credit_posting_job_rollback_guard;
