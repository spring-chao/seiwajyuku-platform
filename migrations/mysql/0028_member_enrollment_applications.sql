-- 0028: public enrollment applications remain separate from formal members
-- until an authorized reviewer completes every server-side enrollment gate.

CREATE TABLE IF NOT EXISTS member_enrollment_links (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(255) NOT NULL,
    token_hash CHAR(64) NOT NULL UNIQUE,
    status VARCHAR(32) NOT NULL,
    active_slot TINYINT NULL UNIQUE,
    created_by BIGINT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    disabled_at DATETIME NULL,
    last_rotated_at DATETIME NULL,
    INDEX idx_enrollment_link_status(status),
    FOREIGN KEY(created_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS member_enrollment_applications (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    application_no VARCHAR(64) NOT NULL UNIQUE,
    link_id BIGINT NOT NULL,
    phone_ciphertext TEXT NOT NULL,
    phone_hash CHAR(64) NOT NULL,
    phone_last4 VARCHAR(4) NOT NULL,
    phone_masked VARCHAR(32) NOT NULL,
    active_phone_guard CHAR(64) NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    gender VARCHAR(32) NULL,
    birthday DATE NULL,
    district VARCHAR(255) NULL,
    company_name VARCHAR(500) NULL,
    position VARCHAR(255) NULL,
    referrer VARCHAR(255) NULL,
    industry_category VARCHAR(255) NULL,
    industry VARCHAR(255) NULL,
    company_products TEXT NULL,
    enterprise_financial_ciphertext TEXT NULL,
    notes TEXT NULL,
    privacy_consent_at DATETIME NOT NULL,
    application_status VARCHAR(32) NOT NULL DEFAULT 'SUBMITTED',
    payment_status VARCHAR(32) NOT NULL DEFAULT 'UNCONFIRMED',
    duplicate_member_risk TINYINT(1) NOT NULL DEFAULT 0,
    org_unit_id VARCHAR(64) NULL,
    join_date DATE NULL,
    reviewed_by BIGINT NULL,
    reviewed_at DATETIME NULL,
    review_note VARCHAR(2000) NULL,
    rejected_by BIGINT NULL,
    rejected_at DATETIME NULL,
    rejection_reason VARCHAR(2000) NULL,
    payment_amount DECIMAL(12,2) NULL,
    payment_note VARCHAR(1000) NULL,
    payment_confirmed_by BIGINT NULL,
    payment_confirmed_at DATETIME NULL,
    converted_member_id BIGINT NULL UNIQUE,
    converted_by BIGINT NULL,
    converted_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    INDEX idx_enrollment_app_status(application_status, created_at),
    INDEX idx_enrollment_app_org(org_unit_id, application_status, created_at),
    INDEX idx_enrollment_app_phone_hash(phone_hash),
    FOREIGN KEY(link_id) REFERENCES member_enrollment_links(id),
    FOREIGN KEY(org_unit_id) REFERENCES org_units(id),
    FOREIGN KEY(reviewed_by) REFERENCES app_users(id),
    FOREIGN KEY(rejected_by) REFERENCES app_users(id),
    FOREIGN KEY(payment_confirmed_by) REFERENCES app_users(id),
    FOREIGN KEY(converted_member_id) REFERENCES members(id),
    FOREIGN KEY(converted_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- IP addresses are never stored. The guard key is a keyed digest of the
-- active link and client address, scoped to one rolling window.
CREATE TABLE IF NOT EXISTS member_enrollment_submission_guards (
    guard_key CHAR(64) PRIMARY KEY,
    window_started_at DATETIME NOT NULL,
    attempt_count INT NOT NULL,
    updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
