-- Configuration rollback is intentionally lossless. Remove 0062 only after
-- the business owner has explicitly cleaned up the confirmed profiles.
START TRANSACTION;
CREATE TEMPORARY TABLE shuku_business_config_rollback_guard (n INT CHECK(n=0));
INSERT INTO shuku_business_config_rollback_guard
SELECT
    (SELECT COUNT(*) FROM enrollment_shuku_contacts)
    + (SELECT COUNT(*) FROM enrollment_shuku_profile_terms)
    + (SELECT COUNT(*) FROM enrollment_shuku_profiles);
DROP TEMPORARY TABLE shuku_business_config_rollback_guard;

DROP TABLE enrollment_shuku_contacts;
DROP TABLE enrollment_shuku_profile_terms;
DELETE FROM schema_migrations
WHERE version='0062_seed_confirmed_shuku_business_configs.sql';
COMMIT;
