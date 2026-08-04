-- 0017: record explicit member profile merges and reference migration.
CREATE TABLE IF NOT EXISTS member_merge_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    survivor_member_id INTEGER NOT NULL REFERENCES members(id),
    duplicate_member_id INTEGER NOT NULL REFERENCES members(id),
    reason TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    merged_by INTEGER REFERENCES app_users(id),
    merged_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_member_merge_history_survivor
    ON member_merge_history(survivor_member_id, merged_at);
