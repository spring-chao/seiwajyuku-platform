-- 0016: audit daily member profile, status and organization changes.
CREATE TABLE IF NOT EXISTS member_change_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id),
    change_type TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    changed_by INTEGER REFERENCES app_users(id),
    changed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_member_change_history_member
    ON member_change_history(member_id, changed_at);
