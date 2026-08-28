-- 0038: store the optional social role for formal party-member profiles.
ALTER TABLE members ADD COLUMN social_role VARCHAR(255) NULL;
