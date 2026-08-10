-- 0019: append-only feedback for privacy-safe member service signals.
CREATE TABLE IF NOT EXISTS member_service_signal_feedback (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    member_id BIGINT NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    signal_code VARCHAR(64) NOT NULL,
    rule_version VARCHAR(64) NOT NULL,
    feedback_status VARCHAR(32) NOT NULL,
    evidence_json TEXT NOT NULL,
    actor_user_id BIGINT NOT NULL,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(member_id) REFERENCES members(id),
    FOREIGN KEY(org_unit_id) REFERENCES org_units(id),
    FOREIGN KEY(actor_user_id) REFERENCES app_users(id),
    INDEX idx_member_signal_feedback_latest(member_id, signal_code, rule_version, created_at, id),
    INDEX idx_member_signal_feedback_actor(actor_user_id, created_at),
    CONSTRAINT chk_member_signal_feedback_status CHECK (
        feedback_status IN ('CONFIRMED_VALID', 'NOT_APPLICABLE', 'DATA_CORRECTED')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
