-- Restore the previous NOT NULL end-date contract.
-- MySQL will reject this ALTER while any open-ended rows remain, preventing
-- a rollback from silently inventing an expiry date.

ALTER TABLE volunteer_appointments
    MODIFY COLUMN ends_at DATETIME NOT NULL;
