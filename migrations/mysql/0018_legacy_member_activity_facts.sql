-- 0018: privacy-safe member activity facts migrated from the legacy operations system.
CREATE TABLE IF NOT EXISTS member_activity_facts (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_system VARCHAR(64) NOT NULL,
    source_table VARCHAR(64) NOT NULL,
    external_id VARCHAR(191) NOT NULL,
    member_id BIGINT NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    activity_type VARCHAR(64) NOT NULL,
    occurred_on DATE NOT NULL,
    participation_status VARCHAR(32) NOT NULL,
    title VARCHAR(255) NULL,
    duration_minutes INT NULL,
    source_updated_at VARCHAR(64) NULL,
    import_batch_id BIGINT NOT NULL,
    imported_at DATETIME NOT NULL,
    FOREIGN KEY(member_id) REFERENCES members(id),
    FOREIGN KEY(org_unit_id) REFERENCES org_units(id),
    FOREIGN KEY(import_batch_id) REFERENCES import_batches(id),
    UNIQUE KEY uq_member_activity_fact_source(source_system, source_table, external_id),
    INDEX idx_member_activity_fact_member(member_id, occurred_on),
    INDEX idx_member_activity_fact_org(org_unit_id, occurred_on, activity_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
