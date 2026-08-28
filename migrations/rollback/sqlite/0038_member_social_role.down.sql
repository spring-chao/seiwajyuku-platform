-- 0038 rollback: remove only the newly added member social-role field.
-- Run only against a verified backup or disposable clone.
ALTER TABLE members DROP COLUMN social_role;
