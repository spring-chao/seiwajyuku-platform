-- 仅在同时回滚生命周期应用代码前执行。
-- 若已经产生新的学习轮次/纠错记录，拒绝回滚，避免丢失生命周期事实。

PRAGMA foreign_keys=OFF;
BEGIN;

CREATE TABLE migration_guard_0045_learning_plan_lifecycle (
    safe_to_rollback INTEGER NOT NULL CHECK(safe_to_rollback=1)
);
INSERT INTO migration_guard_0045_learning_plan_lifecycle(safe_to_rollback)
SELECT CASE WHEN EXISTS(
    SELECT 1 FROM class_learning_bindings
    WHERE learning_round <> 1
       OR start_cycle_index <> 1
       OR ended_at IS NOT NULL
       OR ended_reason IS NOT NULL
       OR previous_binding_id IS NOT NULL
       OR transition_type <> 'INITIAL'
) THEN 0 ELSE 1 END;
DROP TABLE migration_guard_0045_learning_plan_lifecycle;

ALTER TABLE group_learning_cycle_tasks RENAME TO group_learning_cycle_tasks_0045;
ALTER TABLE class_learning_cycle_schedule_overrides RENAME TO class_learning_cycle_schedule_overrides_0045;
ALTER TABLE class_learning_cycles RENAME TO class_learning_cycles_0045;
ALTER TABLE class_learning_bindings RENAME TO class_learning_bindings_0045;

DROP INDEX IF EXISTS idx_group_learning_cycle_tasks_group;
DROP INDEX IF EXISTS idx_class_learning_cycles_current;
DROP INDEX IF EXISTS idx_class_learning_cycle_schedule_overrides_class;
DROP INDEX IF EXISTS idx_class_learning_bindings_previous;
DROP INDEX IF EXISTS idx_class_learning_bindings_lifecycle;
DROP INDEX IF EXISTS idx_class_learning_bindings_class;

CREATE TABLE class_learning_bindings (
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
INSERT INTO class_learning_bindings(
    id, class_org_unit_id, plan_version_id, cohort_month, started_at, status,
    created_by, created_at, updated_at
)
SELECT id, class_org_unit_id, plan_version_id, cohort_month, started_at, status,
       created_by, created_at, updated_at
FROM class_learning_bindings_0045;

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
FROM class_learning_cycles_0045;

CREATE TABLE class_learning_cycle_schedule_overrides (
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
INSERT INTO class_learning_cycle_schedule_overrides(
    id, binding_id, class_org_unit_id, learning_cycle_index,
    planned_class_meeting_at, adjustment_reason, status, created_by,
    updated_by, created_at, updated_at
)
SELECT id, binding_id, class_org_unit_id, learning_cycle_index,
       planned_class_meeting_at, adjustment_reason, status, created_by,
       updated_by, created_at, updated_at
FROM class_learning_cycle_schedule_overrides_0045;

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
FROM group_learning_cycle_tasks_0045;

DROP TABLE group_learning_cycle_tasks_0045;
DROP TABLE class_learning_cycle_schedule_overrides_0045;
DROP TABLE class_learning_cycles_0045;
DROP TABLE class_learning_bindings_0045;

CREATE INDEX idx_class_learning_bindings_class
    ON class_learning_bindings(class_org_unit_id, status, started_at);
CREATE INDEX idx_class_learning_cycles_current
    ON class_learning_cycles(class_org_unit_id, cycle_status, opened_at);
CREATE INDEX idx_class_learning_cycle_schedule_overrides_class
    ON class_learning_cycle_schedule_overrides(class_org_unit_id, status, learning_cycle_index);
CREATE INDEX idx_group_learning_cycle_tasks_group
    ON group_learning_cycle_tasks(group_org_unit_id, status, updated_at);

COMMIT;
PRAGMA foreign_keys=ON;
DELETE FROM schema_migrations
WHERE version='0045_class_learning_plan_lifecycle.sql';
