-- 0029: align public enrollment applications with the 2026 Suzhou application form.
ALTER TABLE member_enrollment_applications
    ADD COLUMN political_status VARCHAR(255) NULL,
    ADD COLUMN company_address VARCHAR(1000) NULL,
    ADD COLUMN email VARCHAR(255) NULL,
    ADD COLUMN invoice_info TEXT NULL,
    ADD COLUMN invoice_type VARCHAR(64) NULL,
    ADD COLUMN employee_count INT NULL,
    ADD COLUMN books_read TEXT NULL,
    ADD COLUMN enrollment_reason_philosophy TEXT NULL,
    ADD COLUMN enrollment_reason_change TEXT NULL,
    ADD COLUMN enrollment_reason_other TEXT NULL,
    ADD COLUMN learning_years_goal VARCHAR(255) NULL,
    ADD COLUMN learning_participation_goal TEXT NULL,
    ADD COLUMN business_goal TEXT NULL,
    ADD COLUMN other_goal TEXT NULL;
