CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    development_org_unit_id TEXT REFERENCES org_units(id),
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    phone_ciphertext TEXT,
    phone_hash TEXT,
    phone_last4 TEXT,
    phone_masked TEXT,
    company_name TEXT,
    enterprise_stage TEXT,
    enterprise_financial_ciphertext TEXT,
    sensitivity_level TEXT NOT NULL DEFAULT 'INTERNAL',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_members_org ON members(org_unit_id, status);
CREATE INDEX IF NOT EXISTS idx_members_phone_hash ON members(phone_hash);

CREATE TABLE IF NOT EXISTS followup_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id),
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    task_type TEXT NOT NULL,
    service_purpose TEXT NOT NULL,
    assigned_user_id INTEGER NOT NULL REFERENCES app_users(id),
    status TEXT NOT NULL DEFAULT 'OPEN',
    confidentiality_level TEXT NOT NULL DEFAULT 'ASSIGNEE',
    due_at TEXT,
    next_followup_at TEXT,
    created_by INTEGER NOT NULL REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_followup_assignee ON followup_tasks(assigned_user_id, status, due_at);

CREATE TABLE IF NOT EXISTS contact_access_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES followup_tasks(id),
    member_id INTEGER NOT NULL REFERENCES members(id),
    actor_user_id INTEGER NOT NULL REFERENCES app_users(id),
    purpose TEXT NOT NULL,
    result TEXT NOT NULL,
    client_reference TEXT,
    accessed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sensitive_export_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER NOT NULL REFERENCES app_users(id),
    export_type TEXT NOT NULL CHECK(export_type IN ('NORMAL','SENSITIVE')),
    org_scope_json TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    purpose TEXT NOT NULL,
    second_confirmed INTEGER NOT NULL DEFAULT 0,
    watermark_text TEXT,
    payload_ciphertext TEXT,
    status TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS export_download_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    export_job_id INTEGER NOT NULL REFERENCES sensitive_export_jobs(id),
    actor_user_id INTEGER NOT NULL REFERENCES app_users(id),
    result TEXT NOT NULL,
    downloaded_at TEXT NOT NULL
);

