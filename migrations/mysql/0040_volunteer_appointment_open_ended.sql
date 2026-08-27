-- 0040: allow volunteer appointments without a known end date.
-- NULL ends_at represents a long-term/open-ended appointment. Existing rows
-- remain unchanged.

ALTER TABLE volunteer_appointments
    MODIFY COLUMN ends_at DATETIME NULL;
