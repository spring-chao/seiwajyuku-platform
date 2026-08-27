-- B2.1: fact-only courses/evidence. Legacy single-course columns are read-only.
-- No plan publication, attendance, organization relation or credit ledger writes.
ALTER TABLE study_meeting_sessions ADD COLUMN course_details_initialized TINYINT NOT NULL DEFAULT 0;
CREATE TABLE study_meeting_courses (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    study_meeting_session_id BIGINT NOT NULL,
    course_key VARCHAR(128) NOT NULL,
    course_name_snapshot VARCHAR(255) NOT NULL,
    course_credit_snapshot INTEGER NULL,
    course_rule_status VARCHAR(16) NOT NULL,
    rule_reference_json TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_meeting_course UNIQUE(study_meeting_session_id, course_key),
    CONSTRAINT chk_meeting_course_points CHECK(
        (course_rule_status='PENDING' AND course_credit_snapshot IS NULL)
        OR (course_rule_status='CONFIGURED' AND course_credit_snapshot BETWEEN 0 AND 999)),
    FOREIGN KEY(study_meeting_session_id) REFERENCES study_meeting_sessions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE study_meeting_evidence (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    study_meeting_session_id BIGINT NOT NULL,
    storage_key VARCHAR(255) NOT NULL UNIQUE,
    storage_backend VARCHAR(16) NOT NULL,
    storage_namespace VARCHAR(512) NOT NULL,
    content_type VARCHAR(32) NOT NULL,
    file_size INTEGER NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    uploaded_by_member_id BIGINT NOT NULL,
    uploaded_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    active_slot INTEGER NULL DEFAULT 1,
    deleted_at DATETIME NULL,
    storage_deleted_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_meeting_active_evidence UNIQUE(study_meeting_session_id, active_slot),
    CONSTRAINT chk_meeting_evidence_slot CHECK(active_slot IS NULL OR active_slot=1),
    CONSTRAINT chk_meeting_evidence_type CHECK(content_type IN ('image/jpeg', 'image/png')),
    CONSTRAINT chk_meeting_evidence_size CHECK(file_size BETWEEN 1 AND 5242880),
    FOREIGN KEY(study_meeting_session_id) REFERENCES study_meeting_sessions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_meeting_evidence_expiry ON study_meeting_evidence(storage_deleted_at, expires_at);
INSERT IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('study_meetings:courses_edit', '修正已提交学习会课程', 'SENSITIVE', UTC_TIMESTAMP());
INSERT IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'study_meetings:courses_edit' FROM roles WHERE role_key IN ('system_admin', 'operations_admin');
