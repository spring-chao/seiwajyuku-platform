-- Refuse to discard recorded referral facts. Prefer application rollback
-- while retaining this additive table once it contains any data.
START TRANSACTION;
CREATE TEMPORARY TABLE enrollment_referrer_rollback_guard (n INT CHECK(n=0));
INSERT INTO enrollment_referrer_rollback_guard
SELECT COUNT(*) FROM enrollment_application_referrer_centers;
DROP TEMPORARY TABLE enrollment_referrer_rollback_guard;
DROP TABLE enrollment_application_referrer_centers;
DELETE FROM schema_migrations WHERE version='0068_enrollment_referrer_centers.sql';
COMMIT;
