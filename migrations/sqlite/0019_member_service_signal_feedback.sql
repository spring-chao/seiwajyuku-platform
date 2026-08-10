-- 0019: append-only feedback for privacy-safe member service signals.
CREATE TABLE IF NOT EXISTS member_service_signal_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id),
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    signal_code TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    feedback_status TEXT NOT NULL CHECK (
        feedback_status IN ('CONFIRMED_VALID', 'NOT_APPLICABLE', 'DATA_CORRECTED')
    ),
    evidence_json TEXT NOT NULL,
    actor_user_id INTEGER NOT NULL REFERENCES app_users(id),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_member_signal_feedback_latest
    ON member_service_signal_feedback(member_id, signal_code, rule_version, created_at, id);
CREATE INDEX IF NOT EXISTS idx_member_signal_feedback_actor
    ON member_service_signal_feedback(actor_user_id, created_at);
