-- 0043 rollback is intentionally destructive: the table contains operator
-- schedule decisions. Export/audit it before running a rollback.
DROP TABLE IF EXISTS class_learning_cycle_schedule_overrides;
