CREATE TABLE IF NOT EXISTS renewal_import_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_name TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  status TEXT NOT NULL,
  preview_json TEXT NOT NULL,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  applied_at TEXT,
  FOREIGN KEY(created_by) REFERENCES app_users(id)
);

CREATE TABLE IF NOT EXISTS renewal_cycles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  member_id INTEGER NOT NULL,
  renewal_year INTEGER NOT NULL,
  org_unit_id TEXT NOT NULL,
  due_month INTEGER NOT NULL,
  original_due_date TEXT,
  plan_code TEXT,
  phase TEXT NOT NULL DEFAULT 'OBSERVE',
  status TEXT NOT NULL DEFAULT 'PENDING_FIRST_CONTACT',
  result TEXT,
  completed_at TEXT,
  assigned_user_id INTEGER,
  source_batch_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(member_id, renewal_year),
  FOREIGN KEY(member_id) REFERENCES members(id),
  FOREIGN KEY(org_unit_id) REFERENCES org_units(id),
  FOREIGN KEY(assigned_user_id) REFERENCES app_users(id),
  FOREIGN KEY(source_batch_id) REFERENCES renewal_import_batches(id)
);

CREATE TABLE IF NOT EXISTS renewal_followups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  renewal_cycle_id INTEGER NOT NULL,
  followed_at TEXT NOT NULL,
  followed_by INTEGER,
  channel TEXT NOT NULL,
  summary TEXT NOT NULL,
  intention TEXT,
  needs_support INTEGER NOT NULL DEFAULT 0,
  next_action TEXT,
  next_followup_at TEXT,
  confidentiality_level TEXT NOT NULL DEFAULT 'ASSIGNEE',
  source_type TEXT NOT NULL DEFAULT 'MANUAL',
  created_at TEXT NOT NULL,
  FOREIGN KEY(renewal_cycle_id) REFERENCES renewal_cycles(id),
  FOREIGN KEY(followed_by) REFERENCES app_users(id)
);

CREATE TABLE IF NOT EXISTS renewal_status_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  renewal_cycle_id INTEGER NOT NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  reason TEXT,
  changed_by INTEGER,
  created_at TEXT NOT NULL,
  FOREIGN KEY(renewal_cycle_id) REFERENCES renewal_cycles(id),
  FOREIGN KEY(changed_by) REFERENCES app_users(id)
);

CREATE TABLE IF NOT EXISTS renewal_import_staging (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER NOT NULL,
  row_no INTEGER NOT NULL,
  match_status TEXT NOT NULL,
  member_id INTEGER,
  org_unit_id TEXT,
  due_month INTEGER,
  proposed_status TEXT,
  history_note TEXT,
  assistance_note TEXT,
  raw_json TEXT NOT NULL,
  issue_code TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(batch_id) REFERENCES renewal_import_batches(id),
  FOREIGN KEY(member_id) REFERENCES members(id)
);
CREATE INDEX IF NOT EXISTS idx_renewal_cycles_year_org ON renewal_cycles(renewal_year, org_unit_id, due_month);
CREATE INDEX IF NOT EXISTS idx_renewal_staging_batch ON renewal_import_staging(batch_id, match_status);
