-- 0032: L1.2-C 课程积分配置（版本化、可审计）
-- 课程积分不写回已确认的标准计划；运营修改只进入独立版本。

CREATE TABLE IF NOT EXISTS learning_plan_credit_rule_versions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    plan_key VARCHAR(128) NOT NULL,
    version_label VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    based_on_version_label VARCHAR(64) NULL,
    created_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_credit_rule_version_status CHECK(status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
    CONSTRAINT uq_credit_rule_version UNIQUE(plan_key, version_label),
    CONSTRAINT fk_credit_rule_version_user FOREIGN KEY(created_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS learning_plan_credit_rules (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    rule_version_id BIGINT NOT NULL,
    course_key VARCHAR(128) NOT NULL,
    course_name VARCHAR(255) NOT NULL,
    year_index INT NULL,
    credit_points INT NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    source VARCHAR(32) NOT NULL DEFAULT 'SYSTEM_DEFAULT',
    aliases_json TEXT NOT NULL,
    created_by BIGINT NULL,
    updated_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_credit_rule_year CHECK(year_index IS NULL OR year_index BETWEEN 1 AND 20),
    CONSTRAINT chk_credit_rule_points CHECK(credit_points BETWEEN 0 AND 999),
    CONSTRAINT chk_credit_rule_status CHECK(status IN ('PENDING', 'CONFIGURED')),
    CONSTRAINT chk_credit_rule_source CHECK(source IN ('BASELINE', 'SYSTEM_DEFAULT', 'OPERATIONS')),
    CONSTRAINT uq_credit_rule UNIQUE(rule_version_id, course_key),
    CONSTRAINT fk_credit_rule_version FOREIGN KEY(rule_version_id) REFERENCES learning_plan_credit_rule_versions(id) ON DELETE CASCADE,
    CONSTRAINT fk_credit_rule_created_user FOREIGN KEY(created_by) REFERENCES app_users(id),
    CONSTRAINT fk_credit_rule_updated_user FOREIGN KEY(updated_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_credit_rule_versions_plan
    ON learning_plan_credit_rule_versions(plan_key, status, version_label);
CREATE INDEX idx_credit_rules_version_year
    ON learning_plan_credit_rules(rule_version_id, year_index, course_name);

INSERT IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('plans:credit_rules_manage', '维护学习计划课程积分标准', 'SENSITIVE', UTC_TIMESTAMP());
INSERT IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'plans:credit_rules_manage'
FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'ops_center_learning', 'ops_center_management');
