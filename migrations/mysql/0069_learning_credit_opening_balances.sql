-- Confirmed cumulative credits only. Independent of course/history migrations.
-- Registers only the opening capability for existing learning staff roles.
-- No individual appointment, course rule or existing ledger row is changed.
CREATE TABLE IF NOT EXISTS learning_credit_opening_imports (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    content_fingerprint CHAR(64) NOT NULL UNIQUE,
    file_sha256 CHAR(64) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    cutoff_date DATE NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING_APPROVAL',
    created_by BIGINT NOT NULL,
    approved_by BIGINT NULL,
    posted_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    approved_at DATETIME NULL,
    posted_at DATETIME NULL,
    CHECK(status IN ('PENDING_APPROVAL','APPROVED','POSTED','CANCELLED')),
    FOREIGN KEY(created_by) REFERENCES app_users(id),
    FOREIGN KEY(approved_by) REFERENCES app_users(id),
    FOREIGN KEY(posted_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS learning_credit_opening_rows (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    import_id BIGINT NOT NULL,
    member_id BIGINT NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    member_name VARCHAR(255) NOT NULL,
    class_label VARCHAR(255) NOT NULL,
    excel_row INT NOT NULL,
    points DECIMAL(10,2) NOT NULL CHECK(points>=0),
    UNIQUE(import_id,member_id),
    FOREIGN KEY(import_id) REFERENCES learning_credit_opening_imports(id),
    FOREIGN KEY(member_id) REFERENCES members(id),
    FOREIGN KEY(org_unit_id) REFERENCES org_units(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS learning_credit_opening_balances (
    member_id BIGINT PRIMARY KEY,
    import_id BIGINT NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    points DECIMAL(10,2) NOT NULL CHECK(points>=0),
    cutoff_date DATE NOT NULL,
    posted_by BIGINT NOT NULL,
    posted_at DATETIME NOT NULL,
    FOREIGN KEY(member_id) REFERENCES members(id),
    FOREIGN KEY(import_id) REFERENCES learning_credit_opening_imports(id),
    FOREIGN KEY(org_unit_id) REFERENCES org_units(id),
    FOREIGN KEY(posted_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO permissions(permission_key,permission_name,sensitive_level,created_at)
VALUES ('plans:credit_opening_manage','上传、复核和入账已确认期初学分','SENSITIVE',UTC_TIMESTAMP());
INSERT IGNORE INTO role_permissions(role_key,permission_key)
SELECT role_key,'plans:credit_opening_manage' FROM roles
WHERE role_key IN ('employee_learning_management','ops_center_learning');
