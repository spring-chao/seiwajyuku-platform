-- Confirmed cumulative credits only. Independent of course/history migrations.
-- Registers only the opening capability for existing learning staff roles.
-- No individual appointment, course rule or existing ledger row is changed.
CREATE TABLE IF NOT EXISTS learning_credit_opening_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_fingerprint CHAR(64) NOT NULL UNIQUE,
    file_sha256 CHAR(64) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    cutoff_date TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING_APPROVAL',
    created_by BIGINT NOT NULL REFERENCES app_users(id),
    approved_by BIGINT NULL REFERENCES app_users(id),
    posted_by BIGINT NULL REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    approved_at TEXT NULL,
    posted_at TEXT NULL,
    CHECK(status IN ('PENDING_APPROVAL','APPROVED','POSTED','CANCELLED'))
);
CREATE TABLE IF NOT EXISTS learning_credit_opening_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id BIGINT NOT NULL REFERENCES learning_credit_opening_imports(id),
    member_id BIGINT NOT NULL REFERENCES members(id),
    org_unit_id VARCHAR(64) NOT NULL REFERENCES org_units(id),
    member_name VARCHAR(255) NOT NULL,
    class_label VARCHAR(255) NOT NULL,
    excel_row INT NOT NULL,
    points DECIMAL(10,2) NOT NULL CHECK(points>=0),
    UNIQUE(import_id,member_id)
);
CREATE TABLE IF NOT EXISTS learning_credit_opening_balances (
    member_id BIGINT PRIMARY KEY REFERENCES members(id),
    import_id BIGINT NOT NULL REFERENCES learning_credit_opening_imports(id),
    org_unit_id VARCHAR(64) NOT NULL REFERENCES org_units(id),
    points DECIMAL(10,2) NOT NULL CHECK(points>=0),
    cutoff_date TEXT NOT NULL,
    posted_by BIGINT NOT NULL REFERENCES app_users(id),
    posted_at TEXT NOT NULL
);

INSERT OR IGNORE INTO permissions(permission_key,permission_name,sensitive_level,created_at)
VALUES ('plans:credit_opening_manage','上传、复核和入账已确认期初学分','SENSITIVE',CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO role_permissions(role_key,permission_key)
SELECT role_key,'plans:credit_opening_manage' FROM roles
WHERE role_key IN ('employee_learning_management','ops_center_learning');
