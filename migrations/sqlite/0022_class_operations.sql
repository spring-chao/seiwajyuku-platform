CREATE TABLE IF NOT EXISTS class_operation_monthly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    period TEXT NOT NULL,
    weekly_meeting_at TEXT,
    planned_class_meeting_at TEXT,
    learning_month INTEGER,
    learning_progress TEXT,
    revenue_growing_member_count INTEGER,
    revenue_comparable_member_count INTEGER,
    updated_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(class_org_unit_id, period)
);
CREATE INDEX IF NOT EXISTS idx_class_operation_period
    ON class_operation_monthly(period, class_org_unit_id);

CREATE TABLE IF NOT EXISTS group_operation_monthly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    period TEXT NOT NULL,
    planned_meeting_at TEXT,
    updated_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(group_org_unit_id, period)
);
CREATE INDEX IF NOT EXISTS idx_group_operation_period
    ON group_operation_monthly(period, group_org_unit_id);
