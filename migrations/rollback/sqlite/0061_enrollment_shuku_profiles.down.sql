-- Configuration rollback is intentionally lossless: do not drop supplied
-- shuku profiles without an explicit data-owner cleanup.
BEGIN;
CREATE TEMP TABLE enrollment_shuku_profile_rollback_guard (n INTEGER CHECK(n=0));
INSERT INTO enrollment_shuku_profile_rollback_guard
SELECT COUNT(*) FROM enrollment_shuku_profiles;
DROP TABLE enrollment_shuku_profile_rollback_guard;

DROP TABLE enrollment_shuku_profiles;
DELETE FROM schema_migrations WHERE version='0061_enrollment_shuku_profiles.sql';
COMMIT;
