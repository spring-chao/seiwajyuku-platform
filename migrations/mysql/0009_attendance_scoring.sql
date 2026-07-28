-- 0009: attendance_scoring - personal attendance and score model
-- Supports three-session checkin (morning/afternoon/konpa) with individual scoring.

-- Event group: one logical class meeting
CREATE TABLE IF NOT EXISTS attendance_event_groups (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_key VARCHAR(128) NOT NULL,
    external_group_id VARCHAR(255) NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    study_org_unit_id VARCHAR(64) NULL,
    title VARCHAR(500) NULL,
    activity_type VARCHAR(64) NOT NULL DEFAULT 'CLASS_MEETING',
    event_date DATE NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    source_updated_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_aeg_source_external UNIQUE (source_key, external_group_id),
    CONSTRAINT fk_aeg_org FOREIGN KEY (org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_aeg_study_org FOREIGN KEY (study_org_unit_id) REFERENCES org_units(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Sessions: morning/afternoon/konpa
CREATE TABLE IF NOT EXISTS attendance_sessions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    event_group_id BIGINT NOT NULL,
    external_session_id VARCHAR(255) NULL,
    session_code VARCHAR(32) NOT NULL,
    session_name VARCHAR(128) NULL,
    session_order INT NOT NULL DEFAULT 0,
    checkin_start_at DATETIME NULL,
    scheduled_start_at DATETIME NULL,
    scheduled_end_at DATETIME NULL,
    checkin_end_at DATETIME NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    source_revision INT NULL,
    source_updated_at DATETIME NULL,
    finalized_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_as_group FOREIGN KEY (event_group_id) REFERENCES attendance_event_groups(id),
    CONSTRAINT chk_as_session_code CHECK(session_code IN ('MORNING', 'AFTERNOON', 'KONPA'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_as_group ON attendance_sessions(event_group_id);
CREATE UNIQUE INDEX uq_as_group_external
    ON attendance_sessions(event_group_id, external_session_id);

-- Attendance records: one per person per session
CREATE TABLE IF NOT EXISTS attendance_records (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    attendance_session_id BIGINT NOT NULL,
    external_record_id VARCHAR(255) NULL,
    external_registration_id VARCHAR(255) NULL,
    member_id BIGINT NULL,
    member_code_snapshot VARCHAR(128) NULL,
    name_snapshot VARCHAR(255) NULL,
    participant_type VARCHAR(32) NOT NULL DEFAULT 'MEMBER',
    score_eligible TINYINT NOT NULL DEFAULT 1,
    attendance_status VARCHAR(32) NOT NULL DEFAULT 'ABSENT',
    checked_at DATETIME NULL,
    checkin_source VARCHAR(32) NULL,
    source_revision INT NULL,
    source_updated_at DATETIME NULL,
    received_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_ar_session FOREIGN KEY (attendance_session_id) REFERENCES attendance_sessions(id),
    CONSTRAINT fk_ar_member FOREIGN KEY (member_id) REFERENCES members(id),
    CONSTRAINT chk_ar_participant_type CHECK(participant_type IN ('MEMBER', 'GUEST', 'OBSERVER')),
    CONSTRAINT chk_ar_status CHECK(attendance_status IN ('PRESENT', 'ABSENT', 'LEAVE', 'MANUAL_PRESENT', 'INVALIDATED', 'UNMATCHED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_ar_session ON attendance_records(attendance_session_id);
CREATE INDEX idx_ar_member ON attendance_records(member_id);
CREATE UNIQUE INDEX uq_ar_session_external ON attendance_records(attendance_session_id, external_record_id);

-- Score rules: versioned, configurable
CREATE TABLE IF NOT EXISTS attendance_score_rules (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    rule_version INT NOT NULL,
    activity_type VARCHAR(64) NOT NULL DEFAULT 'CLASS_MEETING',
    session_code VARCHAR(32) NOT NULL,
    base_points DECIMAL(6,2) NOT NULL,
    late_deduction DECIMAL(6,2) NOT NULL DEFAULT 1,
    early_leave_deduction DECIMAL(6,2) NOT NULL DEFAULT 1,
    effective_from DATE NOT NULL,
    effective_until DATE NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX uq_attendance_score_rule_version
    ON attendance_score_rules(activity_type, session_code, rule_version);

-- Score records: one per attendance record, recalculable
CREATE TABLE IF NOT EXISTS attendance_score_records (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    attendance_record_id BIGINT NOT NULL,
    member_id BIGINT NULL,
    rule_id BIGINT NULL,
    rule_version INT NOT NULL,
    base_points DECIMAL(6,2) NOT NULL,
    late_deduction DECIMAL(6,2) NOT NULL DEFAULT 0,
    early_leave_deduction DECIMAL(6,2) NOT NULL DEFAULT 0,
    other_adjustment DECIMAL(6,2) NOT NULL DEFAULT 0,
    final_points DECIMAL(6,2) NOT NULL,
    is_late TINYINT NOT NULL DEFAULT 0,
    is_early_leave TINYINT NOT NULL DEFAULT 0,
    calculation_detail_json TEXT NULL,
    source_updated_at DATETIME NULL,
    calculated_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_asr_record FOREIGN KEY (attendance_record_id) REFERENCES attendance_records(id),
    CONSTRAINT fk_asr_member FOREIGN KEY (member_id) REFERENCES members(id),
    CONSTRAINT fk_asr_rule FOREIGN KEY (rule_id) REFERENCES attendance_score_rules(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX uq_asr_record ON attendance_score_records(attendance_record_id);

-- Adjudications: manual corrections
CREATE TABLE IF NOT EXISTS attendance_adjudications (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    attendance_record_id BIGINT NOT NULL,
    adjudication_type VARCHAR(32) NOT NULL,
    occurred_at DATETIME NULL,
    reason VARCHAR(500) NOT NULL,
    actor_user_id BIGINT NOT NULL,
    created_at DATETIME NOT NULL,
    superseded_at DATETIME NULL,
    CONSTRAINT fk_aa_record FOREIGN KEY (attendance_record_id) REFERENCES attendance_records(id),
    CONSTRAINT fk_aa_user FOREIGN KEY (actor_user_id) REFERENCES app_users(id),
    CONSTRAINT chk_aa_type CHECK(adjudication_type IN (
        'EARLY_LEAVE', 'CANCEL_EARLY_LEAVE', 'MANUAL_CHECKIN',
        'INVALIDATE_CHECKIN', 'LEAVE', 'CANCEL_LEAVE', 'MEMBER_RELINK'
    ))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_aa_record ON attendance_adjudications(attendance_record_id);

-- Sync runs: incremental pull tracking
CREATE TABLE IF NOT EXISTS attendance_sync_runs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_key VARCHAR(128) NOT NULL,
    cursor_before VARCHAR(255) NULL,
    cursor_after VARCHAR(255) NULL,
    received_sessions INT NOT NULL DEFAULT 0,
    received_records INT NOT NULL DEFAULT 0,
    inserted_count INT NOT NULL DEFAULT 0,
    updated_count INT NOT NULL DEFAULT 0,
    ignored_count INT NOT NULL DEFAULT 0,
    error_count INT NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'RUNNING',
    started_at DATETIME NOT NULL,
    finished_at DATETIME NULL,
    error_summary TEXT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Seed default score rules
INSERT IGNORE INTO attendance_score_rules
    (rule_version, activity_type, session_code, base_points, late_deduction, early_leave_deduction, effective_from, status, created_at)
VALUES
    (1, 'CLASS_MEETING', 'MORNING', 7, 1, 1, '2026-01-01', 'ACTIVE', NOW()),
    (1, 'CLASS_MEETING', 'AFTERNOON', 7, 1, 1, '2026-01-01', 'ACTIVE', NOW()),
    (1, 'CLASS_MEETING', 'KONPA', 4, 1, 1, '2026-01-01', 'ACTIVE', NOW());
