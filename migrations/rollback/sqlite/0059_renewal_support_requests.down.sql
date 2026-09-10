-- Renewal support coordination is operational history. Refuse a lossy
-- rollback once any request has been recorded.
BEGIN;
CREATE TEMP TABLE renewal_support_request_rollback_guard (n INTEGER CHECK(n=0));
INSERT INTO renewal_support_request_rollback_guard
SELECT COUNT(*) FROM renewal_support_requests;
DROP TABLE renewal_support_request_rollback_guard;

DROP TABLE renewal_support_requests;
DELETE FROM schema_migrations WHERE version='0059_renewal_support_requests.sql';
COMMIT;
