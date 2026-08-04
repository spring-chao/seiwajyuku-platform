-- 0015: prevent duplicate member profiles for the same protected phone hash.
CREATE UNIQUE INDEX IF NOT EXISTS uq_members_phone_hash
    ON members(phone_hash);
