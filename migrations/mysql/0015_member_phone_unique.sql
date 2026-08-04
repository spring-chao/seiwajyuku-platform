-- 0015: prevent duplicate member profiles for the same protected phone hash
ALTER TABLE members ADD UNIQUE KEY uq_members_phone_hash(phone_hash);
