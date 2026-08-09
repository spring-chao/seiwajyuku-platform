-- 0015 rollback removes the duplicate-phone protection index.
-- Run only against a verified backup or disposable clone.
DROP INDEX IF EXISTS uq_members_phone_hash;
