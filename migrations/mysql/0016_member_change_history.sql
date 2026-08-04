-- 0016: audit daily member profile, status and organization changes
CREATE TABLE IF NOT EXISTS member_change_history (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    member_id BIGINT NOT NULL,
    change_type VARCHAR(64) NOT NULL,
    before_json JSON NOT NULL,
    after_json JSON NOT NULL,
    changed_by BIGINT NULL,
    changed_at DATETIME NOT NULL,
    FOREIGN KEY(member_id) REFERENCES members(id),
    FOREIGN KEY(changed_by) REFERENCES app_users(id),
    INDEX idx_member_change_history_member(member_id, changed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
