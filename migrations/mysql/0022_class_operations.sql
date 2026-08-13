CREATE TABLE IF NOT EXISTS class_operation_monthly (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    class_org_unit_id VARCHAR(64) NOT NULL,
    period CHAR(7) NOT NULL,
    weekly_meeting_at DATETIME NULL,
    planned_class_meeting_at DATETIME NULL,
    learning_month INT NULL,
    learning_progress TEXT NULL,
    revenue_growing_member_count INT NULL,
    revenue_comparable_member_count INT NULL,
    updated_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_class_operation_period(class_org_unit_id, period),
    INDEX idx_class_operation_period(period, class_org_unit_id),
    CONSTRAINT fk_class_operation_user FOREIGN KEY(updated_by) REFERENCES app_users(id)
);

CREATE TABLE IF NOT EXISTS group_operation_monthly (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    group_org_unit_id VARCHAR(64) NOT NULL,
    period CHAR(7) NOT NULL,
    planned_meeting_at DATETIME NULL,
    updated_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_group_operation_period(group_org_unit_id, period),
    INDEX idx_group_operation_period(period, group_org_unit_id),
    CONSTRAINT fk_group_operation_user FOREIGN KEY(updated_by) REFERENCES app_users(id)
);
