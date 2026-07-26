CREATE TABLE IF NOT EXISTS integration_sources (
    source_key VARCHAR(128) PRIMARY KEY,
    source_type VARCHAR(32) NOT NULL,
    api_key_hash CHAR(64) NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS integration_snapshots (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_key VARCHAR(128) NOT NULL,
    external_id VARCHAR(255) NOT NULL,
    snapshot_type VARCHAR(32) NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    activity_type VARCHAR(64) NOT NULL,
    occurred_at DATETIME NOT NULL,
    participant_hash CHAR(64),
    participant_last4 VARCHAR(8),
    eligible_count INT NOT NULL DEFAULT 0,
    completed_count INT NOT NULL DEFAULT 0,
    title VARCHAR(500),
    status VARCHAR(32) NOT NULL DEFAULT 'COMPLETED',
    received_at DATETIME NOT NULL,
    UNIQUE KEY uq_integration_external(source_key, external_id),
    INDEX idx_integration_metric(snapshot_type, org_unit_id, occurred_at, activity_type),
    CONSTRAINT fk_snapshot_source FOREIGN KEY(source_key) REFERENCES integration_sources(source_key),
    CONSTRAINT fk_snapshot_org FOREIGN KEY(org_unit_id) REFERENCES org_units(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS integration_sync_runs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_key VARCHAR(128) NOT NULL,
    snapshot_type VARCHAR(32) NOT NULL,
    received_count INT NOT NULL,
    inserted_count INT NOT NULL,
    duplicate_count INT NOT NULL,
    status VARCHAR(32) NOT NULL,
    error_summary TEXT,
    started_at DATETIME NOT NULL,
    finished_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
