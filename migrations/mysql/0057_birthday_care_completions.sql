-- 0057: Persist an auditable birthday-care completion even when no class
-- operation rhythm exists for an active member.
CREATE TABLE IF NOT EXISTS birthday_care_completions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    member_id BIGINT NOT NULL,
    birthday_year SMALLINT UNSIGNED NOT NULL,
    due_date DATE NOT NULL,
    channel VARCHAR(16) NOT NULL,
    completed_at DATETIME NOT NULL,
    completed_by BIGINT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_birthday_care_completion_member_year UNIQUE(member_id, birthday_year),
    CONSTRAINT chk_birthday_care_completion_year CHECK(birthday_year BETWEEN 1900 AND 9999),
    CONSTRAINT chk_birthday_care_completion_channel CHECK(channel IN ('WECHAT', 'PHONE')),
    CONSTRAINT fk_birthday_care_completion_member FOREIGN KEY(member_id) REFERENCES members(id),
    CONSTRAINT fk_birthday_care_completion_actor FOREIGN KEY(completed_by) REFERENCES app_users(id),
    INDEX idx_birthday_care_completions_due(birthday_year, due_date, member_id),
    INDEX idx_birthday_care_completions_actor(completed_by, completed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
