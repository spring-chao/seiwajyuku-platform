CREATE TABLE IF NOT EXISTS followup_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES followup_tasks(id),
    member_id INTEGER NOT NULL REFERENCES members(id),
    channel TEXT NOT NULL CHECK(channel IN ('PHONE','WECHAT','MEETING','VISIT','COURSE','OTHER')),
    contacted_at TEXT NOT NULL,
    subject_statement TEXT,
    objective_facts TEXT,
    staff_judgment TEXT,
    outcome_code TEXT NOT NULL,
    next_action TEXT,
    next_followup_at TEXT,
    created_by INTEGER NOT NULL REFERENCES app_users(id),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_followup_records_task ON followup_records(task_id, contacted_at);

CREATE TABLE IF NOT EXISTS enterprise_visit_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES followup_tasks(id),
    member_id INTEGER NOT NULL REFERENCES members(id),
    appointment_at TEXT,
    visited_at TEXT NOT NULL,
    purpose TEXT NOT NULL,
    participants_json TEXT NOT NULL,
    location_type TEXT NOT NULL,
    objective_facts TEXT NOT NULL,
    expressed_needs TEXT,
    support_provided TEXT,
    staff_judgment TEXT,
    next_action TEXT,
    next_followup_at TEXT,
    created_by INTEGER NOT NULL REFERENCES app_users(id),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_enterprise_visits_member ON enterprise_visit_records(member_id, visited_at);
