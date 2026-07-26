CREATE TABLE IF NOT EXISTS members (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    member_code VARCHAR(128) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    development_org_unit_id VARCHAR(64) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    phone_ciphertext TEXT NULL,
    phone_hash CHAR(64) NULL,
    phone_last4 VARCHAR(4) NULL,
    phone_masked VARCHAR(32) NULL,
    company_name VARCHAR(500) NULL,
    enterprise_stage VARCHAR(128) NULL,
    enterprise_financial_ciphertext TEXT NULL,
    sensitivity_level VARCHAR(32) NOT NULL DEFAULT 'INTERNAL',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    INDEX idx_members_org(org_unit_id, status),
    INDEX idx_members_phone_hash(phone_hash),
    FOREIGN KEY(org_unit_id) REFERENCES org_units(id),
    FOREIGN KEY(development_org_unit_id) REFERENCES org_units(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS followup_tasks (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    member_id BIGINT NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    task_type VARCHAR(64) NOT NULL,
    service_purpose VARCHAR(1000) NOT NULL,
    assigned_user_id BIGINT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'OPEN',
    confidentiality_level VARCHAR(32) NOT NULL DEFAULT 'ASSIGNEE',
    due_at DATETIME NULL,
    next_followup_at DATETIME NULL,
    created_by BIGINT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    INDEX idx_followup_assignee(assigned_user_id, status, due_at),
    FOREIGN KEY(member_id) REFERENCES members(id),
    FOREIGN KEY(org_unit_id) REFERENCES org_units(id),
    FOREIGN KEY(assigned_user_id) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS contact_access_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    task_id BIGINT NOT NULL,
    member_id BIGINT NOT NULL,
    actor_user_id BIGINT NOT NULL,
    purpose VARCHAR(1000) NOT NULL,
    result VARCHAR(32) NOT NULL,
    client_reference VARCHAR(255) NULL,
    accessed_at DATETIME NOT NULL,
    FOREIGN KEY(task_id) REFERENCES followup_tasks(id),
    FOREIGN KEY(member_id) REFERENCES members(id),
    FOREIGN KEY(actor_user_id) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS sensitive_export_jobs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    actor_user_id BIGINT NOT NULL,
    export_type VARCHAR(16) NOT NULL,
    org_scope_json JSON NOT NULL,
    fields_json JSON NOT NULL,
    purpose VARCHAR(1000) NOT NULL,
    second_confirmed TINYINT(1) NOT NULL DEFAULT 0,
    watermark_text VARCHAR(500) NULL,
    payload_ciphertext LONGTEXT NULL,
    status VARCHAR(32) NOT NULL,
    expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(actor_user_id) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS export_download_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    export_job_id BIGINT NOT NULL,
    actor_user_id BIGINT NOT NULL,
    result VARCHAR(32) NOT NULL,
    downloaded_at DATETIME NOT NULL,
    FOREIGN KEY(export_job_id) REFERENCES sensitive_export_jobs(id),
    FOREIGN KEY(actor_user_id) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

