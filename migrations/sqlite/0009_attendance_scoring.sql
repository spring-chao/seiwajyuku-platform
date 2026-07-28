-- 0009: attendance_scoring - personal attendance and score model
-- Supports three-session checkin (morning/afternoon/konpa) with individual scoring.

-- Event group: one logical class meeting
CREATE TABLE IF NOT EXISTS attendance_event_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key VARCHAR(128) NOT NULL,
    external_group_id VARCHAR(255) NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL REFERENCES org_units(id),
    study_org_unit_id VARCHAR(64) REFERENCES org_units(id),
    title VARCHAR(500),
    activity_type VARCHAR(64) NOT NULL DEFAULT 'CLASS_MEETING',
    event_date TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    source_updated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_aeg_source_external
    ON attendance_event_groups(source_key, external_group_id);

-- Sessions: morning/afternoon/konpa
CREATE TABLE IF NOT EXISTS attendance_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_group_id INTEGER NOT NULL REFERENCES attendance_event_groups(id),
    external_session_id VARCHAR(255),
    session_code VARCHAR(32) NOT NULL CHECK(session_code IN ('MORNING', 'AFTERNOON', 'KONPA')),
    session_name VARCHAR(128),
    session_order INTEGER NOT NULL DEFAULT 0,
    checkin_start_at TEXT,
    scheduled_start_at TEXT,
    scheduled_end_at TEXT,
    checkin_end_at TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    source_revision INTEGER,
    source_updated_at TEXT,
    finalized_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_as_group ON attendance_sessions(event_group_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_as_group_external
    ON attendance_sessions(event_group_id, external_session_id);

-- Attendance records: one per person per session
CREATE TABLE IF NOT EXISTS attendance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_session_id INTEGER NOT NULL REFERENCES attendance_sessions(id),
    external_record_id VARCHAR(255),
    external_registration_id VARCHAR(255),
    member_id INTEGER REFERENCES members(id),
    member_code_snapshot VARCHAR(128),
    name_snapshot VARCHAR(255),
    participant_type VARCHAR(32) NOT NULL DEFAULT 'MEMBER'
        CHECK(participant_type IN ('MEMBER', 'GUEST', 'OBSERVER')),
    score_eligible INTEGER NOT NULL DEFAULT 1,
    attendance_status VARCHAR(32) NOT NULL DEFAULT 'ABSENT'
        CHECK(attendance_status IN ('PRESENT', 'ABSENT', 'LEAVE', 'MANUAL_PRESENT', 'INVALIDATED', 'UNMATCHED')),
    checked_at TEXT,
    checkin_source VARCHAR(32),
    source_revision INTEGER,
    source_updated_at TEXT,
    received_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ar_session ON attendance_records(attendance_session_id);
CREATE INDEX IF NOT EXISTS idx_ar_member ON attendance_records(member_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ar_session_external
    ON attendance_records(attendance_session_id, external_record_id);

-- Score rules: versioned, configurable
CREATE TABLE IF NOT EXISTS attendance_score_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_version INTEGER NOT NULL,
    activity_type VARCHAR(64) NOT NULL DEFAULT 'CLASS_MEETING',
    session_code VARCHAR(32) NOT NULL,
    base_points REAL NOT NULL,
    late_deduction REAL NOT NULL DEFAULT 1,
    early_leave_deduction REAL NOT NULL DEFAULT 1,
    effective_from TEXT NOT NULL,
    effective_until TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_score_rule_version
    ON attendance_score_rules(activity_type, session_code, rule_version);

-- Score records: one per attendance record, recalculable
CREATE TABLE IF NOT EXISTS attendance_score_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_record_id INTEGER NOT NULL REFERENCES attendance_records(id),
    member_id INTEGER REFERENCES members(id),
    rule_id INTEGER REFERENCES attendance_score_rules(id),
    rule_version INTEGER NOT NULL,
    base_points REAL NOT NULL,
    late_deduction REAL NOT NULL DEFAULT 0,
    early_leave_deduction REAL NOT NULL DEFAULT 0,
    other_adjustment REAL NOT NULL DEFAULT 0,
    final_points REAL NOT NULL,
    is_late INTEGER NOT NULL DEFAULT 0,
    is_early_leave INTEGER NOT NULL DEFAULT 0,
    calculation_detail_json TEXT,
    source_updated_at TEXT,
    calculated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_asr_record
    ON attendance_score_records(attendance_record_id);

-- Adjudications: manual corrections (early leave, cancel, manual checkin, etc.)
CREATE TABLE IF NOT EXISTS attendance_adjudications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_record_id INTEGER NOT NULL REFERENCES attendance_records(id),
    adjudication_type VARCHAR(32) NOT NULL
        CHECK(adjudication_type IN (
            'EARLY_LEAVE', 'CANCEL_EARLY_LEAVE', 'MANUAL_CHECKIN',
            'INVALIDATE_CHECKIN', 'LEAVE', 'CANCEL_LEAVE', 'MEMBER_RELINK'
        )),
    occurred_at TEXT,
    reason VARCHAR(500) NOT NULL,
    actor_user_id INTEGER NOT NULL REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    superseded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_aa_record ON attendance_adjudications(attendance_record_id);

-- Sync runs: incremental pull tracking
CREATE TABLE IF NOT EXISTS attendance_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key VARCHAR(128) NOT NULL,
    cursor_before VARCHAR(255),
    cursor_after VARCHAR(255),
    received_sessions INTEGER NOT NULL DEFAULT 0,
    received_records INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    ignored_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'RUNNING',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_summary TEXT
);

-- Seed default score rules
INSERT OR IGNORE INTO attendance_score_rules
    (rule_version, activity_type, session_code, base_points, late_deduction, early_leave_deduction, effective_from, status, created_at)
VALUES
    (1, 'CLASS_MEETING', 'MORNING', 7, 1, 1, '2026-01-01', 'ACTIVE', datetime('now')),
    (1, 'CLASS_MEETING', 'AFTERNOON', 7, 1, 1, '2026-01-01', 'ACTIVE', datetime('now')),
    (1, 'CLASS_MEETING', 'KONPA', 4, 1, 1, '2026-01-01', 'ACTIVE', datetime('now'));
