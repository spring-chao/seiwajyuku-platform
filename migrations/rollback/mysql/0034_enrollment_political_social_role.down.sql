-- 0034 rollback: remove the optional party-member social-role field.
ALTER TABLE member_enrollment_applications DROP COLUMN social_role;
