-- 0031 rollback is only safe before cohort-specific tracks are imported.
-- The legacy schema cannot represent more than one track per plan/cycle.

ALTER TABLE learning_plan_cycles
    DROP INDEX uq_learning_plan_cycle_track;

DROP INDEX idx_learning_plan_cycles_track ON learning_plan_cycles;

ALTER TABLE learning_plan_cycles
    DROP CHECK chk_learning_plan_cohort_month;

ALTER TABLE learning_plan_cycles
    DROP COLUMN cohort_month_key,
    DROP COLUMN cohort_month,
    ADD CONSTRAINT uq_learning_plan_cycle UNIQUE(plan_version_id, cycle_index);
