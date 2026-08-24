-- 0029: align public enrollment applications with the 2026 Suzhou application form.
ALTER TABLE member_enrollment_applications ADD COLUMN political_status TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN company_address TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN email TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN invoice_info TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN invoice_type TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN employee_count INTEGER;
ALTER TABLE member_enrollment_applications ADD COLUMN books_read TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN enrollment_reason_philosophy TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN enrollment_reason_change TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN enrollment_reason_other TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN learning_years_goal TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN learning_participation_goal TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN business_goal TEXT;
ALTER TABLE member_enrollment_applications ADD COLUMN other_goal TEXT;
