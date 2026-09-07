-- 0050: C7.1.2 HQ daily-reading import batches, source identities, and observations.
-- This migration stores traceable source facts only and creates no ledger entries.

CREATE TABLE IF NOT EXISTS hq_reading_import_batches (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_type VARCHAR(64) NOT NULL DEFAULT 'HQ_READING_EXPORT',
    target_class_org_unit_id VARCHAR(64) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    file_sha256 CHAR(64) NOT NULL,
    imported_at DATETIME NOT NULL,
    imported_by BIGINT NULL,
    row_count INT NOT NULL DEFAULT 0,
    unique_person_count INT NOT NULL DEFAULT 0,
    date_from DATE NULL,
    date_to DATE NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PARSED',
    summary_json TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_hq_reading_batch_source CHECK(source_type='HQ_READING_EXPORT'),
    CONSTRAINT chk_hq_reading_batch_counts CHECK(row_count >= 0 AND unique_person_count >= 0),
    CONSTRAINT chk_hq_reading_batch_status CHECK(status IN ('PARSED', 'MATCHING', 'CONFIRMED', 'DRY_RUN', 'REJECTED')),
    CONSTRAINT uq_hq_reading_batch_file UNIQUE(source_type, target_class_org_unit_id, file_sha256),
    CONSTRAINT fk_hq_reading_batch_class FOREIGN KEY(target_class_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_hq_reading_batch_user FOREIGN KEY(imported_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_hq_reading_batches_class_date
    ON hq_reading_import_batches(target_class_org_unit_id, date_from, date_to, id);

CREATE TABLE IF NOT EXISTS hq_reading_source_identities (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_system VARCHAR(64) NOT NULL DEFAULT 'HQ',
    source_identity_key VARCHAR(255) NOT NULL,
    target_class_org_unit_id VARCHAR(64) NOT NULL,
    member_id BIGINT NOT NULL,
    first_confirmed_at DATETIME NOT NULL,
    first_confirmed_by BIGINT NULL,
    latest_seen_name VARCHAR(255) NOT NULL,
    latest_seen_masked_account VARCHAR(128) NULL,
    latest_seen_group VARCHAR(255) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'CONFIRMED',
    conflict_reason VARCHAR(255) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_hq_reading_identity_status CHECK(status IN ('CONFIRMED', 'CONFLICT')),
    CONSTRAINT uq_hq_reading_source_identity UNIQUE(source_system, source_identity_key),
    CONSTRAINT fk_hq_reading_identity_class FOREIGN KEY(target_class_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_hq_reading_identity_member FOREIGN KEY(member_id) REFERENCES members(id),
    CONSTRAINT fk_hq_reading_identity_user FOREIGN KEY(first_confirmed_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_hq_reading_source_identity_member
    ON hq_reading_source_identities(target_class_org_unit_id, member_id, status);

CREATE TABLE IF NOT EXISTS hq_reading_import_observations (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    batch_id BIGINT NOT NULL,
    first_batch_id BIGINT NOT NULL,
    source_type VARCHAR(64) NOT NULL DEFAULT 'HQ_READING_EXPORT',
    source_id VARCHAR(255) NOT NULL,
    source_identity_key VARCHAR(255) NOT NULL,
    source_row_key VARCHAR(255) NOT NULL,
    source_row_number INT NOT NULL,
    target_class_org_unit_id VARCHAR(64) NOT NULL,
    member_id BIGINT NULL,
    source_name VARCHAR(255) NOT NULL,
    source_masked_account VARCHAR(128) NULL,
    source_group_name VARCHAR(255) NULL,
    mapped_group_org_unit_id VARCHAR(64) NULL,
    group_type VARCHAR(32) NOT NULL,
    source_is_staff TINYINT NOT NULL DEFAULT 0,
    learning_qualification_status VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    recording_raw VARCHAR(255) NULL,
    recording_status VARCHAR(32) NOT NULL,
    occurred_on DATE NOT NULL,
    match_status VARCHAR(64) NOT NULL,
    match_method VARCHAR(64) NULL,
    personal_credit_eligible TINYINT NOT NULL DEFAULT 0,
    class_rate_denominator_eligible TINYINT NOT NULL DEFAULT 0,
    class_rate_numerator_eligible TINYINT NOT NULL DEFAULT 0,
    eligibility_reason VARCHAR(255) NULL,
    content_hash CHAR(64) NOT NULL,
    metadata_json TEXT NOT NULL,
    seen_count INT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_hq_reading_observation_source CHECK(source_type='HQ_READING_EXPORT'),
    CONSTRAINT chk_hq_reading_observation_row CHECK(source_row_number > 0),
    CONSTRAINT chk_hq_reading_observation_group CHECK(group_type IN ('CLASS_REGULAR', 'CLASS_ADVANCED', 'NO_GROUP', 'UNKNOWN')),
    CONSTRAINT chk_hq_reading_observation_staff CHECK(source_is_staff IN (0, 1)),
    CONSTRAINT chk_hq_reading_observation_qualification CHECK(learning_qualification_status IN ('LEARNING', 'NOT_LEARNING', 'UNKNOWN')),
    CONSTRAINT chk_hq_reading_observation_recording CHECK(recording_status IN ('COMPLETE', 'NOT_COMPLETE', 'UNKNOWN')),
    CONSTRAINT chk_hq_reading_observation_match CHECK(match_status IN ('AUTO_MATCHED', 'CANDIDATE', 'CONFIRMED_BINDING', 'MEMBER_MAPPING_REQUIRED', 'SOURCE_IDENTITY_CONFLICT', 'GROUP_MAPPING_MISSING', 'GROUP_MISMATCH', 'REVIEW_REQUIRED', 'NO_GROUP')),
    CONSTRAINT chk_hq_reading_observation_flags CHECK(
        personal_credit_eligible IN (0, 1)
        AND class_rate_denominator_eligible IN (0, 1)
        AND class_rate_numerator_eligible IN (0, 1)
    ),
    CONSTRAINT chk_hq_reading_observation_seen CHECK(seen_count > 0),
    CONSTRAINT uq_hq_reading_observation_source UNIQUE(source_type, source_id),
    CONSTRAINT fk_hq_reading_observation_batch FOREIGN KEY(batch_id) REFERENCES hq_reading_import_batches(id),
    CONSTRAINT fk_hq_reading_observation_first_batch FOREIGN KEY(first_batch_id) REFERENCES hq_reading_import_batches(id),
    CONSTRAINT fk_hq_reading_observation_class FOREIGN KEY(target_class_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_hq_reading_observation_member FOREIGN KEY(member_id) REFERENCES members(id),
    CONSTRAINT fk_hq_reading_observation_group FOREIGN KEY(mapped_group_org_unit_id) REFERENCES org_units(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_hq_reading_observations_batch
    ON hq_reading_import_observations(batch_id, occurred_on, id);
CREATE INDEX idx_hq_reading_observations_class_date
    ON hq_reading_import_observations(target_class_org_unit_id, occurred_on, group_type, id);
CREATE INDEX idx_hq_reading_observations_identity
    ON hq_reading_import_observations(source_identity_key, occurred_on, id);

INSERT IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('plans:hq_reading_import_manage', '导入总部每日读书并进行身份核验', 'SENSITIVE', UTC_TIMESTAMP());
INSERT IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'plans:hq_reading_import_manage'
FROM roles
WHERE role_key IN ('system_admin', 'operations_admin', 'ops_center_learning', 'ops_center_management');
