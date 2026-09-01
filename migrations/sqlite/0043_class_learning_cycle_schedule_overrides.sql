-- 0043: V1.3-B2 班级级学习周期计划调整
-- 只保存某个班级某个周期的计划班会时间覆盖；实际周期仍由班会确认边界推进。

CREATE TABLE IF NOT EXISTS class_learning_cycle_schedule_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    binding_id INTEGER NOT NULL REFERENCES class_learning_bindings(id) ON DELETE CASCADE,
    class_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    learning_cycle_index INTEGER NOT NULL CHECK(learning_cycle_index BETWEEN 1 AND 240),
    planned_class_meeting_at TEXT NOT NULL,
    adjustment_reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK(status IN ('ACTIVE', 'REVOKED')),
    created_by INTEGER REFERENCES app_users(id),
    updated_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(binding_id, learning_cycle_index)
);
CREATE INDEX IF NOT EXISTS idx_class_learning_cycle_schedule_overrides_class
    ON class_learning_cycle_schedule_overrides(class_org_unit_id, status, learning_cycle_index);
