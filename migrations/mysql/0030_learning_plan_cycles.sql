-- 0030: L1 三年学习计划与学习周期引擎
-- 运行时学习进度由真实班会确认推进，不依赖自然月份。

CREATE TABLE IF NOT EXISTS learning_plan_versions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    plan_key VARCHAR(128) NOT NULL,
    plan_name VARCHAR(255) NOT NULL,
    version_label VARCHAR(64) NOT NULL,
    duration_cycles INT NOT NULL DEFAULT 36,
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    source_name VARCHAR(255) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_learning_plan_duration CHECK(duration_cycles BETWEEN 1 AND 240),
    CONSTRAINT chk_learning_plan_status CHECK(status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED', 'RETIRED')),
    CONSTRAINT uq_learning_plan_version UNIQUE(plan_key, version_label)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS learning_plan_cycles (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    plan_version_id BIGINT NOT NULL,
    cycle_index INT NOT NULL,
    year_index INT NOT NULL,
    cycle_label VARCHAR(255) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_learning_cycle_index CHECK(cycle_index BETWEEN 1 AND 240),
    CONSTRAINT chk_learning_cycle_year CHECK(year_index BETWEEN 1 AND 20),
    CONSTRAINT uq_learning_plan_cycle UNIQUE(plan_version_id, cycle_index),
    CONSTRAINT fk_learning_cycle_plan FOREIGN KEY(plan_version_id) REFERENCES learning_plan_versions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_learning_plan_cycles_plan
    ON learning_plan_cycles(plan_version_id, cycle_index);

CREATE TABLE IF NOT EXISTS learning_plan_tasks (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    plan_cycle_id BIGINT NOT NULL,
    task_type VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT NULL,
    credit_points DECIMAL(8,2) NULL,
    is_required TINYINT(1) NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    metadata_json TEXT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_learning_task_cycle FOREIGN KEY(plan_cycle_id) REFERENCES learning_plan_cycles(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_learning_plan_tasks_cycle
    ON learning_plan_tasks(plan_cycle_id, sort_order, id);

CREATE TABLE IF NOT EXISTS class_learning_bindings (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    class_org_unit_id VARCHAR(64) NOT NULL,
    plan_version_id BIGINT NOT NULL,
    cohort_month INT NULL,
    started_at DATETIME NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_class_learning_binding_month CHECK(cohort_month IS NULL OR cohort_month BETWEEN 1 AND 12),
    CONSTRAINT chk_class_learning_binding_status CHECK(status IN ('ACTIVE', 'COMPLETED', 'ENDED')),
    CONSTRAINT fk_class_learning_binding_class FOREIGN KEY(class_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_class_learning_binding_plan FOREIGN KEY(plan_version_id) REFERENCES learning_plan_versions(id),
    CONSTRAINT fk_class_learning_binding_user FOREIGN KEY(created_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_class_learning_bindings_class
    ON class_learning_bindings(class_org_unit_id, status, started_at);

CREATE TABLE IF NOT EXISTS class_learning_cycles (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    binding_id BIGINT NOT NULL,
    class_org_unit_id VARCHAR(64) NOT NULL,
    learning_cycle_index INT NOT NULL,
    plan_cycle_id BIGINT NOT NULL,
    opened_at DATETIME NOT NULL,
    planned_class_meeting_at DATETIME NULL,
    actual_class_meeting_at DATETIME NULL,
    class_meeting_status VARCHAR(32) NOT NULL DEFAULT 'PLANNED',
    group_meeting_policy VARCHAR(32) NOT NULL DEFAULT 'REQUIRED',
    cycle_status VARCHAR(32) NOT NULL DEFAULT 'OPEN',
    closed_at DATETIME NULL,
    adjustment_reason VARCHAR(1000) NULL,
    source_event_group_id BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_class_learning_meeting_status CHECK(class_meeting_status IN ('PLANNED', 'POSTPONED', 'HELD')),
    CONSTRAINT chk_class_learning_group_policy CHECK(group_meeting_policy IN ('REQUIRED', 'SUSPENDED', 'WAIVED')),
    CONSTRAINT chk_class_learning_cycle_status CHECK(cycle_status IN ('UPCOMING', 'OPEN', 'CLOSED')),
    CONSTRAINT fk_class_learning_cycle_binding FOREIGN KEY(binding_id) REFERENCES class_learning_bindings(id) ON DELETE CASCADE,
    CONSTRAINT fk_class_learning_cycle_class FOREIGN KEY(class_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_class_learning_cycle_plan FOREIGN KEY(plan_cycle_id) REFERENCES learning_plan_cycles(id),
    CONSTRAINT fk_class_learning_cycle_event FOREIGN KEY(source_event_group_id) REFERENCES attendance_event_groups(id),
    CONSTRAINT uq_class_learning_cycle UNIQUE(binding_id, learning_cycle_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_class_learning_cycles_current
    ON class_learning_cycles(class_org_unit_id, cycle_status, opened_at);

CREATE TABLE IF NOT EXISTS group_learning_cycle_tasks (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    class_learning_cycle_id BIGINT NOT NULL,
    group_org_unit_id VARCHAR(64) NOT NULL,
    plan_task_id BIGINT NULL,
    task_type VARCHAR(64) NOT NULL DEFAULT 'GROUP_MEETING',
    task_title VARCHAR(255) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    completed_at DATETIME NULL,
    adjusted_by BIGINT NULL,
    note VARCHAR(2000) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_group_learning_task_status CHECK(status IN ('PENDING', 'COMPLETED', 'WAIVED', 'MISSED')),
    CONSTRAINT fk_group_learning_task_cycle FOREIGN KEY(class_learning_cycle_id) REFERENCES class_learning_cycles(id) ON DELETE CASCADE,
    CONSTRAINT fk_group_learning_task_group FOREIGN KEY(group_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_group_learning_task_plan FOREIGN KEY(plan_task_id) REFERENCES learning_plan_tasks(id),
    CONSTRAINT fk_group_learning_task_user FOREIGN KEY(adjusted_by) REFERENCES app_users(id),
    CONSTRAINT uq_group_learning_task UNIQUE(class_learning_cycle_id, group_org_unit_id, task_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_group_learning_cycle_tasks_group
    ON group_learning_cycle_tasks(group_org_unit_id, status, updated_at);
