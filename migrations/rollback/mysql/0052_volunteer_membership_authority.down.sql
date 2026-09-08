-- 0052 rollback is loss-averse. Do not discard formal member links.

CREATE TEMPORARY TABLE volunteer_membership_rollback_guard (n INT CHECK(n=0));
INSERT INTO volunteer_membership_rollback_guard
SELECT COUNT(*) FROM volunteer_appointments WHERE member_id IS NOT NULL;
DROP TEMPORARY TABLE volunteer_membership_rollback_guard;

ALTER TABLE volunteer_appointments
    DROP FOREIGN KEY fk_volunteer_appointments_member,
    DROP INDEX idx_volunteer_appointments_member,
    DROP COLUMN member_id;

DELETE FROM schema_migrations WHERE version='0052_volunteer_membership_authority.sql';
