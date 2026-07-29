CREATE TABLE IF NOT EXISTS operating_institutions (
    id VARCHAR(64) PRIMARY KEY,
    institution_code VARCHAR(128) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    institution_type VARCHAR(32) NOT NULL,
    parent_id VARCHAR(64) NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    INDEX idx_operating_institutions_parent(parent_id, is_active),
    FOREIGN KEY(parent_id) REFERENCES operating_institutions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS institution_org_links (
    institution_id VARCHAR(64) NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    link_type VARCHAR(32) NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (institution_id, org_unit_id, link_type),
    FOREIGN KEY(institution_id) REFERENCES operating_institutions(id) ON DELETE CASCADE,
    FOREIGN KEY(org_unit_id) REFERENCES org_units(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS person_profiles (
    id VARCHAR(64) PRIMARY KEY,
    display_name VARCHAR(255) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS account_person_links (
    user_id BIGINT PRIMARY KEY,
    person_id VARCHAR(64) NOT NULL,
    linked_at DATETIME NOT NULL,
    linked_by BIGINT NULL,
    source_reference VARCHAR(500) NULL,
    INDEX idx_account_person_links_person(person_id),
    FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
    FOREIGN KEY(person_id) REFERENCES person_profiles(id),
    FOREIGN KEY(linked_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS member_identities (
    member_id BIGINT PRIMARY KEY,
    person_id VARCHAR(64) NOT NULL,
    membership_started_on DATE NULL,
    membership_ended_on DATE NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    source_reference VARCHAR(500) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    INDEX idx_member_identities_person(person_id),
    FOREIGN KEY(member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY(person_id) REFERENCES person_profiles(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS member_membership_periods (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    member_id BIGINT NOT NULL,
    membership_year INT NOT NULL,
    annual_fee_amount_cents INT NULL,
    fee_status VARCHAR(16) NOT NULL DEFAULT 'UNCONFIRMED',
    valid_from DATE NULL,
    valid_until DATE NULL,
    source_reference VARCHAR(500) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_member_membership_year(member_id, membership_year),
    FOREIGN KEY(member_id) REFERENCES member_identities(member_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS operations_employments (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    person_id VARCHAR(64) NOT NULL,
    institution_id VARCHAR(64) NOT NULL,
    employment_status VARCHAR(16) NOT NULL,
    started_on DATE NULL,
    ended_on DATE NULL,
    source_reference VARCHAR(500) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    INDEX idx_operations_employments_person(person_id, employment_status),
    FOREIGN KEY(person_id) REFERENCES person_profiles(id),
    FOREIGN KEY(institution_id) REFERENCES operating_institutions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS operations_position_assignments (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    employment_id BIGINT NOT NULL,
    position_key VARCHAR(64) NOT NULL,
    valid_from DATETIME NULL,
    valid_until DATETIME NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    source_reference VARCHAR(500) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    INDEX idx_position_assignments_employment(employment_id, status, valid_from, valid_until),
    FOREIGN KEY(employment_id) REFERENCES operations_employments(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS employee_service_responsibilities (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    employment_id BIGINT NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    scope_type VARCHAR(16) NOT NULL,
    valid_from DATETIME NULL,
    valid_until DATETIME NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    source_reference VARCHAR(500) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    INDEX idx_employee_service_responsibilities(employment_id, status, valid_from, valid_until),
    FOREIGN KEY(employment_id) REFERENCES operations_employments(id) ON DELETE CASCADE,
    FOREIGN KEY(org_unit_id) REFERENCES org_units(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS volunteer_appointments (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    person_id VARCHAR(64) NOT NULL,
    appointment_key VARCHAR(64) NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    scope_type VARCHAR(16) NOT NULL,
    starts_at DATETIME NOT NULL,
    ends_at DATETIME NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'PLANNED',
    source_reference VARCHAR(500) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    INDEX idx_volunteer_appointments_person(person_id, status, starts_at, ends_at),
    INDEX idx_volunteer_appointments_org(org_unit_id, status, starts_at, ends_at),
    FOREIGN KEY(person_id) REFERENCES person_profiles(id),
    FOREIGN KEY(org_unit_id) REFERENCES org_units(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS technical_admin_assignments (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    person_id VARCHAR(64) NOT NULL,
    assignment_purpose VARCHAR(500) NOT NULL,
    starts_at DATETIME NOT NULL,
    ends_at DATETIME NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'PLANNED',
    source_reference VARCHAR(500) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    INDEX idx_technical_admin_assignments_person(person_id, status, starts_at, ends_at),
    FOREIGN KEY(person_id) REFERENCES person_profiles(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO operating_institutions
    (id, institution_code, name, institution_type, parent_id, is_active, created_at, updated_at)
VALUES
    ('institution-seiwa-hq', 'SEIWA_HQ', '盛和塾总部', 'HEADQUARTERS', NULL, 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('institution-jiangnan', 'JIANGNAN', '江南塾', 'DIVISION', 'institution-seiwa-hq', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('institution-suzhou', 'SUZHOU_CENTER', '苏州分中心', 'CITY_CENTER', 'institution-jiangnan', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('institution-suzhou-operations', 'SUZHOU_OPERATIONS_CENTER', '苏州分中心运营中心', 'OPERATIONS_CENTER', 'institution-suzhou', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP());

INSERT IGNORE INTO institution_org_links(institution_id, org_unit_id, link_type, created_at)
SELECT 'institution-suzhou', id, 'LEGACY_REPRESENTATION', UTC_TIMESTAMP()
FROM org_units WHERE unit_code='SZ_ROOT';
