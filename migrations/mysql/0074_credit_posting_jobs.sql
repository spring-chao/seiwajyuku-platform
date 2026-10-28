-- Durable confirmations only. Existing source, batch and ledger rules are unchanged.
CREATE TABLE learning_credit_posting_jobs (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 import_id BIGINT NOT NULL UNIQUE,
 actor_user_id BIGINT NOT NULL,
 confirmed_fingerprint CHAR(64) NOT NULL,
 resume_partial_failure BOOLEAN NOT NULL DEFAULT FALSE,
 status VARCHAR(32) NOT NULL,
 pause_requested BOOLEAN NOT NULL DEFAULT FALSE,
 lease_token CHAR(32) NULL,
 lease_until DATETIME NULL,
 error_code VARCHAR(64) NULL,
 created_at DATETIME NOT NULL,
 updated_at DATETIME NOT NULL,
 CHECK(status IN ('PENDING','RUNNING','PAUSED','SUCCEEDED','NEEDS_REVIEW')),
 FOREIGN KEY(import_id) REFERENCES learning_credit_source_imports(id),
 FOREIGN KEY(actor_user_id) REFERENCES app_users(id),
 INDEX idx_credit_posting_jobs_ready(status,updated_at,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
