-- B2.1: fact-only courses/evidence. Legacy single-course columns are read-only.
-- No plan publication, attendance, organization relation or credit ledger writes.
ALTER TABLE study_meeting_sessions ADD COLUMN course_details_initialized INTEGER NOT NULL DEFAULT 0;
CREATE TABLE study_meeting_courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_meeting_session_id INTEGER NOT NULL,
    course_key TEXT NOT NULL,
    course_name_snapshot TEXT NOT NULL,
    course_credit_snapshot INTEGER NULL,
    course_rule_status TEXT NOT NULL,
    rule_reference_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT uq_meeting_course UNIQUE(study_meeting_session_id, course_key),
    CONSTRAINT chk_meeting_course_points CHECK(
        (course_rule_status='PENDING' AND course_credit_snapshot IS NULL)
        OR (course_rule_status='CONFIGURED' AND course_credit_snapshot BETWEEN 0 AND 999)),
    FOREIGN KEY(study_meeting_session_id) REFERENCES study_meeting_sessions(id) ON DELETE CASCADE
);
CREATE TABLE study_meeting_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_meeting_session_id INTEGER NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    storage_backend TEXT NOT NULL,
    storage_namespace TEXT NOT NULL,
    content_type TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    uploaded_by_member_id INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    active_slot INTEGER NULL DEFAULT 1,
    deleted_at TEXT NULL,
    storage_deleted_at TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT uq_meeting_active_evidence UNIQUE(study_meeting_session_id, active_slot),
    CONSTRAINT chk_meeting_evidence_slot CHECK(active_slot IS NULL OR active_slot=1),
    CONSTRAINT chk_meeting_evidence_type CHECK(content_type IN ('image/jpeg', 'image/png')),
    CONSTRAINT chk_meeting_evidence_size CHECK(file_size BETWEEN 1 AND 5242880),
    FOREIGN KEY(study_meeting_session_id) REFERENCES study_meeting_sessions(id)
);
CREATE INDEX idx_meeting_evidence_expiry ON study_meeting_evidence(storage_deleted_at, expires_at);
INSERT OR IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('study_meetings:courses_edit', '修正已提交学习会课程', 'SENSITIVE', datetime('now'));
INSERT OR IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'study_meetings:courses_edit' FROM roles WHERE role_key IN ('system_admin', 'operations_admin');
