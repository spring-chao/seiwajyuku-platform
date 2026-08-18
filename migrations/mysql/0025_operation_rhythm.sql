-- 0025: operation rhythm templates, monthly cycles, work items and progress records.
CREATE TABLE IF NOT EXISTS operation_templates (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    template_code VARCHAR(64) NOT NULL,
    name VARCHAR(255) NOT NULL,
    scope_type VARCHAR(32) NOT NULL,
    description TEXT NULL,
    is_active TINYINT NOT NULL DEFAULT 1,
    created_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_operation_template_code(template_code),
    CONSTRAINT fk_operation_template_user FOREIGN KEY(created_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS operation_template_nodes (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    template_id BIGINT NOT NULL,
    node_code VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    category VARCHAR(64) NOT NULL,
    rule_type VARCHAR(64) NOT NULL,
    rule_config_json TEXT NOT NULL,
    start_offset_days INT NOT NULL DEFAULT 0,
    due_offset_days INT NOT NULL DEFAULT 0,
    responsibility_role VARCHAR(128) NULL,
    external_responsibility_role VARCHAR(128) NULL,
    business_type VARCHAR(64) NULL,
    sort_order INT NOT NULL DEFAULT 0,
    is_active TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_operation_template_node(template_id, node_code),
    INDEX idx_operation_template_nodes_order(template_id, sort_order, id),
    CONSTRAINT fk_operation_template_node_template FOREIGN KEY(template_id) REFERENCES operation_templates(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS operation_cycles (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    template_id BIGINT NOT NULL,
    period CHAR(7) NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    generated_by BIGINT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_operation_cycle_scope(template_id, period, org_unit_id),
    INDEX idx_operation_cycles_period(period, org_unit_id),
    CONSTRAINT fk_operation_cycle_template FOREIGN KEY(template_id) REFERENCES operation_templates(id),
    CONSTRAINT fk_operation_cycle_org FOREIGN KEY(org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_operation_cycle_user FOREIGN KEY(generated_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS operation_items (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    cycle_id BIGINT NOT NULL,
    node_id BIGINT NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    period CHAR(7) NOT NULL,
    item_key VARCHAR(191) NOT NULL,
    title VARCHAR(255) NOT NULL,
    category VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    responsibility_role VARCHAR(128) NULL,
    external_responsibility_role VARCHAR(128) NULL,
    start_date DATE NULL,
    due_date DATE NULL,
    actual_at DATETIME NULL,
    completion_note TEXT NULL,
    business_type VARCHAR(64) NULL,
    business_id VARCHAR(64) NULL,
    manual_override TINYINT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_operation_item_cycle_key(cycle_id, item_key),
    INDEX idx_operation_items_period(period, org_unit_id, due_date, status),
    INDEX idx_operation_items_business(business_type, business_id, period),
    CONSTRAINT fk_operation_item_cycle FOREIGN KEY(cycle_id) REFERENCES operation_cycles(id),
    CONSTRAINT fk_operation_item_node FOREIGN KEY(node_id) REFERENCES operation_template_nodes(id),
    CONSTRAINT fk_operation_item_org FOREIGN KEY(org_unit_id) REFERENCES org_units(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS operation_progress_records (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    item_id BIGINT NOT NULL,
    status VARCHAR(32) NOT NULL,
    note TEXT NULL,
    occurred_at DATETIME NOT NULL,
    actor_user_id BIGINT NOT NULL,
    source_type VARCHAR(32) NOT NULL DEFAULT 'MANUAL',
    created_at DATETIME NOT NULL,
    INDEX idx_operation_progress_item(item_id, occurred_at),
    CONSTRAINT fk_operation_progress_item FOREIGN KEY(item_id) REFERENCES operation_items(id),
    CONSTRAINT fk_operation_progress_user FOREIGN KEY(actor_user_id) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
