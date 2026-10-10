-- Empty entry-code metadata only; no production data, IAM assignment or check-in writes.
CREATE TABLE IF NOT EXISTS attendance_entry_tokens (
    token_hash TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    created_by INTEGER NOT NULL REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_attendance_entry_event ON attendance_entry_tokens(event_id, revoked_at);
