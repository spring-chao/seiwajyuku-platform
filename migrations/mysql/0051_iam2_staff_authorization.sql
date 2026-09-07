-- 0051: IAM 2.0 explicit employee role-and-scope authorization.
-- Existing position-derived authorization remains available until an
-- employment has explicit grants, and no personnel data is migrated here.

ALTER TABLE operations_employments
    ADD COLUMN department_name VARCHAR(255) NULL AFTER institution_id,
    ADD COLUMN supervisor_user_id BIGINT NULL AFTER department_name,
    ADD INDEX idx_operations_employments_department(department_name, employment_status),
    ADD CONSTRAINT fk_operations_employment_supervisor
        FOREIGN KEY(supervisor_user_id) REFERENCES app_users(id);

CREATE TABLE IF NOT EXISTS employee_profile_details (
    person_id VARCHAR(64) PRIMARY KEY,
    work_phone_ciphertext TEXT NULL,
    work_phone_hash CHAR(64) NULL UNIQUE,
    work_phone_last4 VARCHAR(4) NULL,
    work_phone_masked VARCHAR(32) NULL,
    gender VARCHAR(16) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_employee_profile_gender
        CHECK(gender IS NULL OR gender IN ('MALE', 'FEMALE', 'UNSPECIFIED')),
    CONSTRAINT fk_employee_profile_person
        FOREIGN KEY(person_id) REFERENCES person_profiles(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS employee_authorization_grants (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    employment_id BIGINT NOT NULL,
    role_key VARCHAR(128) NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    scope_type VARCHAR(16) NOT NULL,
    valid_from DATETIME NULL,
    valid_until DATETIME NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    source_reference VARCHAR(500) NULL,
    created_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_employee_authorization_scope
        CHECK(scope_type IN ('UNIT', 'SUBTREE')),
    CONSTRAINT chk_employee_authorization_status
        CHECK(status IN ('PLANNED', 'ACTIVE', 'SUSPENDED', 'ENDED', 'REVOKED')),
    CONSTRAINT chk_employee_authorization_dates
        CHECK(valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from),
    CONSTRAINT fk_employee_authorization_employment
        FOREIGN KEY(employment_id) REFERENCES operations_employments(id) ON DELETE CASCADE,
    CONSTRAINT fk_employee_authorization_role
        FOREIGN KEY(role_key) REFERENCES roles(role_key),
    CONSTRAINT fk_employee_authorization_org
        FOREIGN KEY(org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_employee_authorization_creator
        FOREIGN KEY(created_by) REFERENCES app_users(id),
    INDEX idx_employee_authorization_grants_employment(employment_id, status, valid_from, valid_until),
    INDEX idx_employee_authorization_grants_scope(org_unit_id, role_key, status, valid_from, valid_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
