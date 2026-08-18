-- 0025: operation rhythm templates, monthly cycles, work items and progress records.
-- This layer stores the operating mechanism only; it does not replace attendance,
-- member, renewal or follow-up facts.
CREATE TABLE IF NOT EXISTS operation_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    description TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_template_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL REFERENCES operation_templates(id),
    node_code TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    rule_config_json TEXT NOT NULL DEFAULT '{}',
    start_offset_days INTEGER NOT NULL DEFAULT 0,
    due_offset_days INTEGER NOT NULL DEFAULT 0,
    responsibility_role TEXT,
    external_responsibility_role TEXT,
    business_type TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(template_id, node_code)
);
CREATE INDEX IF NOT EXISTS idx_operation_template_nodes_order
    ON operation_template_nodes(template_id, sort_order, id);

CREATE TABLE IF NOT EXISTS operation_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL REFERENCES operation_templates(id),
    period TEXT NOT NULL,
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    generated_by INTEGER REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(template_id, period, org_unit_id)
);
CREATE INDEX IF NOT EXISTS idx_operation_cycles_period
    ON operation_cycles(period, org_unit_id);

CREATE TABLE IF NOT EXISTS operation_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL REFERENCES operation_cycles(id),
    node_id INTEGER NOT NULL REFERENCES operation_template_nodes(id),
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    period TEXT NOT NULL,
    item_key TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    responsibility_role TEXT,
    external_responsibility_role TEXT,
    start_date TEXT,
    due_date TEXT,
    actual_at TEXT,
    completion_note TEXT,
    business_type TEXT,
    business_id TEXT,
    manual_override INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(cycle_id, item_key)
);
CREATE INDEX IF NOT EXISTS idx_operation_items_period
    ON operation_items(period, org_unit_id, due_date, status);
CREATE INDEX IF NOT EXISTS idx_operation_items_business
    ON operation_items(business_type, business_id, period);

CREATE TABLE IF NOT EXISTS operation_progress_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES operation_items(id),
    status TEXT NOT NULL,
    note TEXT,
    occurred_at TEXT NOT NULL,
    actor_user_id INTEGER NOT NULL REFERENCES app_users(id),
    source_type TEXT NOT NULL DEFAULT 'MANUAL',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operation_progress_item
    ON operation_progress_records(item_id, occurred_at);
