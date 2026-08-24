-- 0031 rollback is deliberately fail-closed when cohort-specific rows exist:
-- 0030 cannot represent distinct 1/4/7/10 month tracks without losing data.

PRAGMA foreign_keys=OFF;
BEGIN;

CREATE TABLE migration_guard_0031_learning_plan_cohort_tracks (
    safe_to_rollback INTEGER NOT NULL CHECK(safe_to_rollback=1)
);
INSERT INTO migration_guard_0031_learning_plan_cohort_tracks(safe_to_rollback)
SELECT CASE WHEN EXISTS(
    SELECT 1 FROM learning_plan_cycles WHERE cohort_month IS NOT NULL
) THEN 0 ELSE 1 END;
DROP TABLE migration_guard_0031_learning_plan_cohort_tracks;

ALTER TABLE group_learning_cycle_tasks RENAME TO group_learning_cycle_tasks_0031;
ALTER TABLE class_learning_cycles RENAME TO class_learning_cycles_0031;
ALTER TABLE learning_plan_tasks RENAME TO learning_plan_tasks_0031;
ALTER TABLE learning_plan_cycles RENAME TO learning_plan_cycles_0031;

CREATE TABLE learning_plan_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_version_id INTEGER NOT NULL REFERENCES learning_plan_versions(id) ON DELETE CASCADE,
    cycle_index INTEGER NOT NULL CHECK(cycle_index BETWEEN 1 AND 240),
    year_index INTEGER NOT NULL CHECK(year_index BETWEEN 1 AND 20),
    cycle_label TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(plan_version_id, cycle_index)
);
INSERT INTO learning_plan_cycles(id, plan_version_id, cycle_index, year_index, cycle_label, created_at, updated_at)
SELECT id, plan_version_id, cycle_index, year_index, cycle_label, created_at, updated_at
FROM learning_plan_cycles_0031;

CREATE TABLE learning_plan_tasks (
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
INSERT INTO learning_plan_tasks(
    id, plan_cycle_id, task_type, title, description, credit_points, is_required,
    sort_order, metadata_json, created_at, updated_at
)
SELECT id, plan_cycle_id, task_type, title, description, credit_points, is_required,
       sort_order, metadata_json, created_at, updated_at
FROM learning_plan_tasks_0031;

CREATE TABLE class_learning_cycles (
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
INSERT INTO class_learning_cycles(
    id, binding_id, class_org_unit_id, learning_cycle_index, plan_cycle_id, opened_at,
    planned_class_meeting_at, actual_class_meeting_at, class_meeting_status,
    group_meeting_policy, cycle_status, closed_at, adjustment_reason,
    source_event_group_id, created_at, updated_at
)
SELECT id, binding_id, class_org_unit_id, learning_cycle_index, plan_cycle_id, opened_at,
       planned_class_meeting_at, actual_class_meeting_at, class_meeting_status,
       group_meeting_policy, cycle_status, closed_at, adjustment_reason,
       source_event_group_id, created_at, updated_at
FROM class_learning_cycles_0031;

CREATE TABLE group_learning_cycle_tasks (
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
INSERT INTO group_learning_cycle_tasks(
    id, class_learning_cycle_id, group_org_unit_id, plan_task_id, task_type,
    task_title, status, completed_at, adjusted_by, note, created_at, updated_at
)
SELECT id, class_learning_cycle_id, group_org_unit_id, plan_task_id, task_type,
       task_title, status, completed_at, adjusted_by, note, created_at, updated_at
FROM group_learning_cycle_tasks_0031;

DROP TABLE group_learning_cycle_tasks_0031;
DROP TABLE class_learning_cycles_0031;
DROP TABLE learning_plan_tasks_0031;
DROP TABLE learning_plan_cycles_0031;

CREATE INDEX idx_learning_plan_cycles_plan
    ON learning_plan_cycles(plan_version_id, cycle_index);
CREATE INDEX idx_learning_plan_tasks_cycle
    ON learning_plan_tasks(plan_cycle_id, sort_order, id);
CREATE INDEX idx_class_learning_cycles_current
    ON class_learning_cycles(class_org_unit_id, cycle_status, opened_at);
CREATE INDEX idx_group_learning_cycle_tasks_group
    ON group_learning_cycle_tasks(group_org_unit_id, status, updated_at);

COMMIT;
PRAGMA foreign_keys=ON;
