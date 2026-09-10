-- Rollback is intentionally destructive only to the additive nullable fields.
-- Refuse a lossy rollback once any link or application has recorded a target.
BEGIN;
CREATE TEMP TABLE enrollment_target_shuku_rollback_guard (n INTEGER CHECK(n=0));
INSERT INTO enrollment_target_shuku_rollback_guard
SELECT (
    (SELECT COUNT(*) FROM member_enrollment_links
     WHERE target_shuku_org_unit_id IS NOT NULL)
    +
    (SELECT COUNT(*) FROM member_enrollment_applications
     WHERE target_shuku_org_unit_id IS NOT NULL)
);
DROP TABLE enrollment_target_shuku_rollback_guard;

DROP INDEX IF EXISTS idx_enrollment_app_target_shuku;
DROP INDEX IF EXISTS idx_enrollment_link_target_shuku;
ALTER TABLE member_enrollment_applications DROP COLUMN target_shuku_org_unit_id;
ALTER TABLE member_enrollment_links DROP COLUMN target_shuku_org_unit_id;
DELETE FROM schema_migrations WHERE version='0060_enrollment_target_shuku.sql';
COMMIT;
