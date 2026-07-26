CREATE TABLE IF NOT EXISTS metric_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    default_unit TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metric_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_definition_id INTEGER NOT NULL REFERENCES metric_definitions(id),
    year INTEGER NOT NULL,
    version INTEGER NOT NULL,
    aggregation_type TEXT NOT NULL,
    period_value_type TEXT NOT NULL,
    unit TEXT NOT NULL,
    data_source_type TEXT NOT NULL,
    null_policy TEXT NOT NULL,
    formula_text TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    created_at TEXT NOT NULL,
    UNIQUE(metric_definition_id, year, version)
);

CREATE TABLE IF NOT EXISTS annual_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    version INTEGER NOT NULL,
    policy_text TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    write_enabled INTEGER NOT NULL DEFAULT 0,
    business_approval_reference TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(year, version)
);

CREATE TABLE IF NOT EXISTS plan_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    annual_plan_id INTEGER NOT NULL REFERENCES annual_plans(id) ON DELETE CASCADE,
    metric_version_id INTEGER NOT NULL REFERENCES metric_versions(id),
    display_order INTEGER NOT NULL,
    weight REAL,
    applicable_unit_types TEXT,
    UNIQUE(annual_plan_id, metric_version_id)
);

CREATE TABLE IF NOT EXISTS org_metric_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    annual_plan_id INTEGER NOT NULL REFERENCES annual_plans(id) ON DELETE CASCADE,
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    metric_version_id INTEGER NOT NULL REFERENCES metric_versions(id),
    annual_target REAL,
    value_state TEXT NOT NULL DEFAULT 'NO_DATA',
    balance_mode TEXT NOT NULL DEFAULT 'ALLOW_VARIANCE',
    variance_reason TEXT,
    owner_user_id INTEGER REFERENCES app_users(id),
    source_reference TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(annual_plan_id, org_unit_id, metric_version_id)
);

CREATE TABLE IF NOT EXISTS metric_period_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    annual_plan_id INTEGER NOT NULL REFERENCES annual_plans(id) ON DELETE CASCADE,
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    metric_version_id INTEGER NOT NULL REFERENCES metric_versions(id),
    period_type TEXT NOT NULL CHECK(period_type IN ('MONTH','HALF_YEAR','YEAR')),
    period_no INTEGER NOT NULL,
    value_kind TEXT NOT NULL CHECK(value_kind IN ('MP','FORECAST','ACTUAL')),
    numeric_value REAL,
    value_state TEXT NOT NULL CHECK(value_state IN ('VALUE','NO_DATA','NOT_APPLICABLE','NOT_DUE','ZERO_IS_VALID')),
    source_type TEXT NOT NULL DEFAULT 'IMPORT',
    source_reference TEXT,
    calculation_detail_json TEXT,
    is_manual_override INTEGER NOT NULL DEFAULT 0,
    updated_by INTEGER REFERENCES app_users(id),
    updated_at TEXT NOT NULL,
    UNIQUE(annual_plan_id, org_unit_id, metric_version_id, period_type, period_no, value_kind)
);

CREATE TABLE IF NOT EXISTS metric_calculation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_version_id INTEGER NOT NULL REFERENCES metric_versions(id),
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    period_type TEXT NOT NULL,
    period_no INTEGER NOT NULL,
    numerator REAL,
    denominator REAL,
    result_value REAL,
    source_period TEXT,
    detail_json TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metric_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_period_value_id INTEGER NOT NULL REFERENCES metric_period_values(id),
    automatic_value REAL,
    override_value REAL,
    reason TEXT NOT NULL,
    evidence_reference TEXT,
    actor_user_id INTEGER NOT NULL REFERENCES app_users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    preview_json TEXT NOT NULL,
    created_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER REFERENCES import_batches(id),
    issue_code TEXT NOT NULL,
    severity TEXT NOT NULL,
    location TEXT,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS annual_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    annual_plan_id INTEGER NOT NULL REFERENCES annual_plans(id) ON DELETE CASCADE,
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    action_type TEXT NOT NULL,
    title TEXT NOT NULL,
    owner_user_id INTEGER REFERENCES app_users(id),
    status TEXT NOT NULL DEFAULT 'DRAFT',
    target_date TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    annual_action_id INTEGER NOT NULL REFERENCES annual_actions(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'NOT_STARTED',
    completed_at TEXT
);

