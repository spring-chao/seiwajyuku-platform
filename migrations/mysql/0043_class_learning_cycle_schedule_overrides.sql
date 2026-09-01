-- 0043: V1.3-B2 班级级学习周期计划调整
-- 只保存某个班级某个周期的计划班会时间覆盖；实际周期仍由班会确认边界推进。

CREATE TABLE IF NOT EXISTS class_learning_cycle_schedule_overrides (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    binding_id BIGINT NOT NULL,
    class_org_unit_id VARCHAR(64) NOT NULL,
    learning_cycle_index INT NOT NULL,
    planned_class_meeting_at DATETIME NOT NULL,
    adjustment_reason VARCHAR(1000) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    created_by BIGINT NULL,
    updated_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_class_learning_cycle_schedule_index
        CHECK(learning_cycle_index BETWEEN 1 AND 240),
    CONSTRAINT chk_class_learning_cycle_schedule_status
        CHECK(status IN ('ACTIVE', 'REVOKED')),
    CONSTRAINT fk_class_learning_cycle_schedule_binding
        FOREIGN KEY(binding_id) REFERENCES class_learning_bindings(id) ON DELETE CASCADE,
    CONSTRAINT fk_class_learning_cycle_schedule_class
        FOREIGN KEY(class_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_class_learning_cycle_schedule_created_by
        FOREIGN KEY(created_by) REFERENCES app_users(id),
    CONSTRAINT fk_class_learning_cycle_schedule_updated_by
        FOREIGN KEY(updated_by) REFERENCES app_users(id),
    CONSTRAINT uq_class_learning_cycle_schedule
        UNIQUE(binding_id, learning_cycle_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_class_learning_cycle_schedule_overrides_class
    ON class_learning_cycle_schedule_overrides(class_org_unit_id, status, learning_cycle_index);
