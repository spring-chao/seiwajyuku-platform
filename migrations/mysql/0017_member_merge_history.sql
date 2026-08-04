-- 0017: record explicit member profile merges and reference migration
CREATE TABLE IF NOT EXISTS member_merge_history (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    survivor_member_id BIGINT NOT NULL,
    duplicate_member_id BIGINT NOT NULL,
    reason VARCHAR(1000) NOT NULL,
    before_json JSON NOT NULL,
    after_json JSON NOT NULL,
    merged_by BIGINT NULL,
    merged_at DATETIME NOT NULL,
    FOREIGN KEY(survivor_member_id) REFERENCES members(id),
    FOREIGN KEY(duplicate_member_id) REFERENCES members(id),
    FOREIGN KEY(merged_by) REFERENCES app_users(id),
    INDEX idx_member_merge_history_survivor(survivor_member_id, merged_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
