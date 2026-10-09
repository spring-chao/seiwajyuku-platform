-- 0068: add an optional review fact without rewriting historical applications.
CREATE TABLE IF NOT EXISTS enrollment_application_referrer_centers (
    application_id BIGINT PRIMARY KEY,
    referrer_org_unit_id VARCHAR(64) NOT NULL,
    updated_by BIGINT NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_enrollment_referrer_application
        FOREIGN KEY(application_id) REFERENCES member_enrollment_applications(id),
    CONSTRAINT fk_enrollment_referrer_center
        FOREIGN KEY(referrer_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_enrollment_referrer_actor
        FOREIGN KEY(updated_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
