CREATE TABLE IF NOT EXISTS metric_definitions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    metric_key VARCHAR(128) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(128) NOT NULL,
    default_unit VARCHAR(32) NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS metric_versions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    metric_definition_id BIGINT NOT NULL,
    year INT NOT NULL,
    version INT NOT NULL,
    aggregation_type VARCHAR(32) NOT NULL,
    period_value_type VARCHAR(32) NOT NULL,
    unit VARCHAR(32) NOT NULL,
    data_source_type VARCHAR(32) NOT NULL,
    null_policy VARCHAR(32) NOT NULL,
    formula_text TEXT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    created_at DATETIME NOT NULL,
    UNIQUE KEY uq_metric_version(metric_definition_id, year, version),
    FOREIGN KEY(metric_definition_id) REFERENCES metric_definitions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS annual_plans (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    year INT NOT NULL,
    version INT NOT NULL,
    policy_text TEXT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    write_enabled TINYINT(1) NOT NULL DEFAULT 0,
    business_approval_reference VARCHAR(500) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_annual_plan(year, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS plan_metrics (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    annual_plan_id BIGINT NOT NULL,
    metric_version_id BIGINT NOT NULL,
    display_order INT NOT NULL,
    weight DECIMAL(12,4) NULL,
    applicable_unit_types VARCHAR(500) NULL,
    UNIQUE KEY uq_plan_metric(annual_plan_id, metric_version_id),
    FOREIGN KEY(annual_plan_id) REFERENCES annual_plans(id) ON DELETE CASCADE,
    FOREIGN KEY(metric_version_id) REFERENCES metric_versions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS org_metric_targets (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    annual_plan_id BIGINT NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    metric_version_id BIGINT NOT NULL,
    annual_target DECIMAL(20,6) NULL,
    value_state VARCHAR(32) NOT NULL DEFAULT 'NO_DATA',
    balance_mode VARCHAR(32) NOT NULL DEFAULT 'ALLOW_VARIANCE',
    variance_reason VARCHAR(1000) NULL,
    owner_user_id BIGINT NULL,
    source_reference VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_org_target(annual_plan_id, org_unit_id, metric_version_id),
    FOREIGN KEY(annual_plan_id) REFERENCES annual_plans(id) ON DELETE CASCADE,
    FOREIGN KEY(org_unit_id) REFERENCES org_units(id),
    FOREIGN KEY(metric_version_id) REFERENCES metric_versions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS metric_period_values (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    annual_plan_id BIGINT NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    metric_version_id BIGINT NOT NULL,
    period_type VARCHAR(16) NOT NULL,
    period_no INT NOT NULL,
    value_kind VARCHAR(16) NOT NULL,
    numeric_value DECIMAL(20,6) NULL,
    value_state VARCHAR(32) NOT NULL,
    source_type VARCHAR(32) NOT NULL DEFAULT 'IMPORT',
    source_reference VARCHAR(1000) NULL,
    calculation_detail_json JSON NULL,
    is_manual_override TINYINT(1) NOT NULL DEFAULT 0,
    updated_by BIGINT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_period_value(annual_plan_id, org_unit_id, metric_version_id, period_type, period_no, value_kind),
    FOREIGN KEY(annual_plan_id) REFERENCES annual_plans(id) ON DELETE CASCADE,
    FOREIGN KEY(org_unit_id) REFERENCES org_units(id),
    FOREIGN KEY(metric_version_id) REFERENCES metric_versions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS metric_calculation_runs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    metric_version_id BIGINT NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    period_type VARCHAR(16) NOT NULL,
    period_no INT NOT NULL,
    numerator DECIMAL(20,6) NULL,
    denominator DECIMAL(20,6) NULL,
    result_value DECIMAL(20,6) NULL,
    source_period VARCHAR(64) NULL,
    detail_json JSON NULL,
    status VARCHAR(32) NOT NULL,
    created_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS metric_overrides (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    metric_period_value_id BIGINT NOT NULL,
    automatic_value DECIMAL(20,6) NULL,
    override_value DECIMAL(20,6) NULL,
    reason VARCHAR(1000) NOT NULL,
    evidence_reference VARCHAR(1000) NULL,
    actor_user_id BIGINT NOT NULL,
    created_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS import_batches (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    import_type VARCHAR(64) NOT NULL,
    source_name VARCHAR(255) NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    preview_json JSON NOT NULL,
    created_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    applied_at DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS data_quality_issues (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    batch_id BIGINT NULL,
    issue_code VARCHAR(128) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    location VARCHAR(500) NULL,
    message VARCHAR(2000) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'OPEN',
    created_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS annual_actions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    annual_plan_id BIGINT NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    action_type VARCHAR(64) NOT NULL,
    title VARCHAR(500) NOT NULL,
    owner_user_id BIGINT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    target_date DATE NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS action_milestones (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    annual_action_id BIGINT NOT NULL,
    title VARCHAR(500) NOT NULL,
    due_date DATE NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'NOT_STARTED',
    completed_at DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

