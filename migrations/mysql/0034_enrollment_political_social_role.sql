-- 0034: capture an optional social role for party-member applicants.
-- Political status remains a plain text field for legacy compatibility, and the
-- public form offers the two standard choices and only asks for this field
-- when the applicant selects 党员.

ALTER TABLE member_enrollment_applications
    ADD COLUMN social_role VARCHAR(255) NULL;
