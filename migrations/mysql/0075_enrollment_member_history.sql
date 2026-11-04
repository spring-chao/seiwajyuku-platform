-- Add the FK-supporting nonunique index before dropping the unique constraint.
ALTER TABLE member_enrollment_applications ADD INDEX idx_enrollment_converted_member (converted_member_id);
ALTER TABLE member_enrollment_applications DROP INDEX converted_member_id;
