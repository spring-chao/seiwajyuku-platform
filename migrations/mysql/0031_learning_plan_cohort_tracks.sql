-- 0031: L1.2-A 四期开班学习轨道
-- cohort_month 保留 NULL 作为通用模板；生成键把 NULL 规范为 0，保证每个版本、
-- 每个学习周期只能有一条通用轨道或一条指定批次轨道。

ALTER TABLE learning_plan_cycles
    DROP INDEX uq_learning_plan_cycle;

ALTER TABLE learning_plan_cycles
    ADD COLUMN cohort_month TINYINT NULL AFTER plan_version_id,
    ADD COLUMN cohort_month_key TINYINT GENERATED ALWAYS AS (COALESCE(cohort_month, 0)) STORED AFTER cohort_month,
    ADD CONSTRAINT chk_learning_plan_cohort_month CHECK(cohort_month IS NULL OR cohort_month BETWEEN 1 AND 12),
    ADD CONSTRAINT uq_learning_plan_cycle_track UNIQUE(plan_version_id, cohort_month_key, cycle_index);

CREATE INDEX idx_learning_plan_cycles_track
    ON learning_plan_cycles(plan_version_id, cohort_month, cycle_index);
