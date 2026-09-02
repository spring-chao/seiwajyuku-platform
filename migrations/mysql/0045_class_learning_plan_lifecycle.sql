-- 0045: 班级学习计划生命周期
-- 每一次重新开始/接续都是新的 binding；历史 binding 与学习事实不删除。

-- 兼容旧安装的 ARCHIVED 存储值，同时允许新安装直接使用 RETIRED。
ALTER TABLE learning_plan_versions
    DROP CHECK chk_learning_plan_status,
    ADD CONSTRAINT chk_learning_plan_status
        CHECK(status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED', 'RETIRED'));

ALTER TABLE class_learning_bindings
    ADD COLUMN learning_round INT NOT NULL DEFAULT 1 AFTER id,
    ADD COLUMN start_cycle_index INT NOT NULL DEFAULT 1 AFTER cohort_month,
    ADD COLUMN ended_at DATETIME NULL AFTER status,
    ADD COLUMN ended_reason VARCHAR(1000) NULL AFTER ended_at,
    ADD COLUMN previous_binding_id BIGINT NULL AFTER ended_reason,
    ADD COLUMN transition_type VARCHAR(32) NOT NULL DEFAULT 'INITIAL' AFTER previous_binding_id,
    ADD CONSTRAINT chk_class_learning_binding_round CHECK(learning_round BETWEEN 1 AND 240),
    ADD CONSTRAINT chk_class_learning_binding_start_cycle CHECK(start_cycle_index BETWEEN 1 AND 240),
    ADD CONSTRAINT chk_class_learning_binding_transition CHECK(
        transition_type IN ('INITIAL', 'RESTART', 'RESUME', 'PLAN_SWITCH', 'CORRECTION')
    ),
    ADD CONSTRAINT fk_class_learning_binding_previous
        FOREIGN KEY(previous_binding_id) REFERENCES class_learning_bindings(id);

CREATE INDEX idx_class_learning_bindings_lifecycle
    ON class_learning_bindings(class_org_unit_id, learning_round, status, started_at);
CREATE INDEX idx_class_learning_bindings_previous
    ON class_learning_bindings(previous_binding_id);
