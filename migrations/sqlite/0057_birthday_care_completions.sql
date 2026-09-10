-- 0057: Persist an auditable birthday-care completion even when no class
-- operation rhythm exists for an active member.
CREATE TABLE IF NOT EXISTS birthday_care_completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id),
    birthday_year INTEGER NOT NULL CHECK(birthday_year BETWEEN 1900 AND 9999),
    due_date TEXT NOT NULL,
    channel TEXT NOT NULL CHECK(channel IN ('WECHAT', 'PHONE')),
    completed_at TEXT NOT NULL,
    completed_by INTEGER NOT NULL REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(member_id, birthday_year)
);
CREATE INDEX IF NOT EXISTS idx_birthday_care_completions_due
    ON birthday_care_completions(birthday_year, due_date, member_id);
CREATE INDEX IF NOT EXISTS idx_birthday_care_completions_actor
    ON birthday_care_completions(completed_by, completed_at);
