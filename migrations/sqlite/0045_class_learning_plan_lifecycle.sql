-- 0045: 班级学习计划生命周期
-- 每一次重新开始/接续都是新的 binding；历史 binding 与学习事实不删除。

ALTER TABLE class_learning_bindings
    ADD COLUMN learning_round INTEGER NOT NULL DEFAULT 1
        CHECK(learning_round BETWEEN 1 AND 240);
ALTER TABLE class_learning_bindings
    ADD COLUMN start_cycle_index INTEGER NOT NULL DEFAULT 1
        CHECK(start_cycle_index BETWEEN 1 AND 240);
ALTER TABLE class_learning_bindings
    ADD COLUMN ended_at TEXT;
ALTER TABLE class_learning_bindings
    ADD COLUMN ended_reason TEXT;
ALTER TABLE class_learning_bindings
    ADD COLUMN previous_binding_id INTEGER REFERENCES class_learning_bindings(id);
ALTER TABLE class_learning_bindings
    ADD COLUMN transition_type TEXT NOT NULL DEFAULT 'INITIAL'
        CHECK(transition_type IN ('INITIAL', 'RESTART', 'RESUME', 'PLAN_SWITCH', 'CORRECTION'));

CREATE INDEX IF NOT EXISTS idx_class_learning_bindings_lifecycle
    ON class_learning_bindings(class_org_unit_id, learning_round, status, started_at);
CREATE INDEX IF NOT EXISTS idx_class_learning_bindings_previous
    ON class_learning_bindings(previous_binding_id);
