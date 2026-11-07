-- NULL keeps existing Gregorian reminders. Lunar month/day is stored as
-- YYYY-MM-DD, with a trailing L for a leap month.
ALTER TABLE members ADD COLUMN birthday_lunar VARCHAR(11) NULL;
