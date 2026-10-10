-- Empty entry-code metadata only; no production data, IAM assignment or check-in writes.
CREATE TABLE IF NOT EXISTS attendance_entry_tokens (
    token_hash CHAR(64) PRIMARY KEY,
    event_id VARCHAR(128) NOT NULL,
    created_by BIGINT NOT NULL,
    created_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    revoked_at DATETIME NULL,
    CONSTRAINT fk_attendance_entry_creator FOREIGN KEY(created_by) REFERENCES app_users(id),
    INDEX idx_attendance_entry_event(event_id, revoked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
