CREATE TABLE IF NOT EXISTS renewal_import_batches (
  id BIGINT PRIMARY KEY AUTO_INCREMENT, source_name VARCHAR(255) NOT NULL, source_sha256 VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL, preview_json JSON NOT NULL, created_by BIGINT NULL, created_at DATETIME NOT NULL,
  applied_at DATETIME NULL, FOREIGN KEY(created_by) REFERENCES app_users(id)
);
CREATE TABLE IF NOT EXISTS renewal_cycles (
  id BIGINT PRIMARY KEY AUTO_INCREMENT, member_id BIGINT NOT NULL, renewal_year INT NOT NULL, org_unit_id VARCHAR(64) NOT NULL,
  due_month TINYINT NOT NULL, original_due_date DATE NULL, plan_code VARCHAR(64) NULL, phase VARCHAR(32) NOT NULL DEFAULT 'OBSERVE',
  status VARCHAR(64) NOT NULL DEFAULT 'PENDING_FIRST_CONTACT', result VARCHAR(64) NULL, completed_at DATETIME NULL,
  assigned_user_id BIGINT NULL, source_batch_id BIGINT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
  UNIQUE KEY uq_renewal_cycle_member_year(member_id, renewal_year),
  FOREIGN KEY(member_id) REFERENCES members(id), FOREIGN KEY(org_unit_id) REFERENCES org_units(id),
  FOREIGN KEY(assigned_user_id) REFERENCES app_users(id), FOREIGN KEY(source_batch_id) REFERENCES renewal_import_batches(id),
  INDEX idx_renewal_cycles_year_org(renewal_year, org_unit_id, due_month)
);
CREATE TABLE IF NOT EXISTS renewal_followups (
  id BIGINT PRIMARY KEY AUTO_INCREMENT, renewal_cycle_id BIGINT NOT NULL, followed_at DATETIME NOT NULL,
  followed_by BIGINT NULL, channel VARCHAR(32) NOT NULL, summary TEXT NOT NULL, intention VARCHAR(64) NULL,
  needs_support TINYINT NOT NULL DEFAULT 0, next_action TEXT NULL, next_followup_at DATETIME NULL,
  confidentiality_level VARCHAR(32) NOT NULL DEFAULT 'ASSIGNEE', source_type VARCHAR(32) NOT NULL DEFAULT 'MANUAL',
  created_at DATETIME NOT NULL, FOREIGN KEY(renewal_cycle_id) REFERENCES renewal_cycles(id), FOREIGN KEY(followed_by) REFERENCES app_users(id)
);
CREATE TABLE IF NOT EXISTS renewal_status_history (
  id BIGINT PRIMARY KEY AUTO_INCREMENT, renewal_cycle_id BIGINT NOT NULL, from_status VARCHAR(64) NULL,
  to_status VARCHAR(64) NOT NULL, reason TEXT NULL, changed_by BIGINT NULL, created_at DATETIME NOT NULL,
  FOREIGN KEY(renewal_cycle_id) REFERENCES renewal_cycles(id), FOREIGN KEY(changed_by) REFERENCES app_users(id)
);
CREATE TABLE IF NOT EXISTS renewal_import_staging (
  id BIGINT PRIMARY KEY AUTO_INCREMENT, batch_id BIGINT NOT NULL, row_no INT NOT NULL, match_status VARCHAR(32) NOT NULL,
  member_id BIGINT NULL, org_unit_id VARCHAR(64) NULL, due_month TINYINT NULL, proposed_status VARCHAR(64) NULL,
  history_note TEXT NULL, assistance_note TEXT NULL, raw_json JSON NOT NULL, issue_code VARCHAR(64) NULL, created_at DATETIME NOT NULL,
  FOREIGN KEY(batch_id) REFERENCES renewal_import_batches(id), FOREIGN KEY(member_id) REFERENCES members(id),
  INDEX idx_renewal_staging_batch(batch_id, match_status)
);
