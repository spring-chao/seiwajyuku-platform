-- 0015 rollback removes the duplicate-phone protection index.
-- Run only against a verified backup or disposable clone.
ALTER TABLE members DROP INDEX uq_members_phone_hash;
