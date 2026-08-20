-- 0027: preserve the source workbook's class committee name on the member profile.
ALTER TABLE members ADD COLUMN class_committee_name VARCHAR(255) NULL;
