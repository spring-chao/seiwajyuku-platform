-- 0030: L1 三年学习计划与学习周期引擎
-- 运行时学习进度由真实班会确认推进，不依赖自然月份。

CREATE TABLE IF NOT EXISTS learning_plan_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_key TEXT NOT NULL,
    plan_name TEXT NOT NULL,
    version_label TEXT NOT NULL,
    duration_cycles INTEGER NOT NULL DEFAULT 36 CHECK(duration_cycles BETWEEN 1 AND 240),
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK(status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED', 'RETIRED')),
    source_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(plan_key, version_label)
);

CREATE TABLE IF NOT EXISTS learning_plan_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_version_id INTEGER NOT NULL REFERENCES learning_plan_versions(id) ON DELETE CASCADE,
    cycle_index INTEGER NOT NULL CHECK(cycle_index BETWEEN 1 AND 240),
    year_index INTEGER NOT NULL CHECK(year_index BETWEEN 1 AND 20),
    cycle_label TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(plan_version_id, cycle_index)
);
CREATE INDEX IF NOT EXISTS idx_learning_plan_cycles_plan
    ON learning_plan_cycles(plan_version_id, cycle_index);

CREATE TABLE IF NOT EXISTS learning_plan_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_cycle_id INTEGER NOT NULL REFERENCES learning_plan_cycles(id) ON DELETE CASCADE,
    task_type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    credit_points REAL,
    is_required INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_plan_tasks_cycle
    ON learning_plan_tasks(plan_cycle_id, sort_order, id);

CREATE TABLE IF NOT EXISTS class_learning_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    plan_version_id INTEGER NOT NULL REFERENCES learning_plan_versions(id),
    cohort_month INTEGER CHECK(cohort_month BETWEEN 1 AND 12),
    started_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK(status IN ('ACTIVE', 'COMPLETED', 'ENDED')),
    created_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_class_learning_bindings_class
    ON class_learning_bindings(class_org_unit_id, status, started_at);

CREATE TABLE IF NOT EXISTS class_learning_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    binding_id INTEGER NOT NULL REFERENCES class_learning_bindings(id) ON DELETE CASCADE,
    class_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    learning_cycle_index INTEGER NOT NULL CHECK(learning_cycle_index BETWEEN 1 AND 240),
    plan_cycle_id INTEGER NOT NULL REFERENCES learning_plan_cycles(id),
    opened_at TEXT NOT NULL,
    planned_class_meeting_at TEXT,
    actual_class_meeting_at TEXT,
    class_meeting_status TEXT NOT NULL DEFAULT 'PLANNED'
        CHECK(class_meeting_status IN ('PLANNED', 'POSTPONED', 'HELD')),
    group_meeting_policy TEXT NOT NULL DEFAULT 'REQUIRED'
        CHECK(group_meeting_policy IN ('REQUIRED', 'SUSPENDED', 'WAIVED')),
    cycle_status TEXT NOT NULL DEFAULT 'OPEN'
        CHECK(cycle_status IN ('UPCOMING', 'OPEN', 'CLOSED')),
    closed_at TEXT,
    adjustment_reason TEXT,
    source_event_group_id INTEGER REFERENCES attendance_event_groups(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(binding_id, learning_cycle_index)
);
CREATE INDEX IF NOT EXISTS idx_class_learning_cycles_current
    ON class_learning_cycles(class_org_unit_id, cycle_status, opened_at);

CREATE TABLE IF NOT EXISTS group_learning_cycle_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_learning_cycle_id INTEGER NOT NULL REFERENCES class_learning_cycles(id) ON DELETE CASCADE,
    group_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    plan_task_id INTEGER REFERENCES learning_plan_tasks(id),
    task_type TEXT NOT NULL DEFAULT 'GROUP_MEETING',
    task_title TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(status IN ('PENDING', 'COMPLETED', 'WAIVED', 'MISSED')),
    completed_at TEXT,
    adjusted_by INTEGER REFERENCES app_users(id),
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(class_learning_cycle_id, group_org_unit_id, task_type)
);
CREATE INDEX IF NOT EXISTS idx_group_learning_cycle_tasks_group
    ON group_learning_cycle_tasks(group_org_unit_id, status, updated_at);
