CREATE TABLE learning_credit_posting_jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 import_id INTEGER NOT NULL UNIQUE REFERENCES learning_credit_source_imports(id),
 actor_user_id INTEGER NOT NULL REFERENCES app_users(id),
 confirmed_fingerprint TEXT NOT NULL,
 resume_partial_failure INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL CHECK(status IN ('PENDING','RUNNING','PAUSED','SUCCEEDED','NEEDS_REVIEW')),
 pause_requested INTEGER NOT NULL DEFAULT 0,
 lease_token TEXT NULL,
 lease_until TEXT NULL,
 error_code TEXT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX idx_credit_posting_jobs_ready ON learning_credit_posting_jobs(status,updated_at,id);
