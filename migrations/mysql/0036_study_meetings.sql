-- 0036: V1.2 MVP-B 小组学习会事实记录
-- 不复用 attendance，不推进班级学习周期，不写正式积分账本。

CREATE TABLE IF NOT EXISTS study_meeting_sessions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    session_code VARCHAR(64) NOT NULL,
    class_org_unit_id VARCHAR(64) NOT NULL,
    study_group_org_unit_id VARCHAR(64) NOT NULL,
    learning_cycle_id BIGINT NOT NULL,
    meeting_date DATE NOT NULL,
    created_by_member_id BIGINT NOT NULL,
    created_by_role VARCHAR(32) NOT NULL,
    has_course TINYINT NOT NULL DEFAULT 0,
    course_key VARCHAR(128) NULL,
    course_name_snapshot VARCHAR(255) NULL,
    course_credit_snapshot INT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'DRAFT',
    submitted_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_study_meeting_session_code UNIQUE(session_code),
    CONSTRAINT chk_study_meeting_creator_role CHECK(created_by_role IN ('GROUP_LEADER', 'CLASS_COUNSELOR')),
    CONSTRAINT chk_study_meeting_has_course CHECK(has_course IN (0, 1)),
    CONSTRAINT chk_study_meeting_status CHECK(status IN ('DRAFT', 'SUBMITTED', 'CANCELLED')),
    CONSTRAINT chk_study_meeting_course CHECK((has_course=0 AND course_key IS NULL) OR (has_course=1 AND course_key IS NOT NULL)),
    CONSTRAINT fk_study_meeting_class FOREIGN KEY(class_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_study_meeting_group FOREIGN KEY(study_group_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_study_meeting_cycle FOREIGN KEY(learning_cycle_id) REFERENCES class_learning_cycles(id),
    CONSTRAINT fk_study_meeting_creator FOREIGN KEY(created_by_member_id) REFERENCES members(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_study_meeting_sessions_group_cycle
    ON study_meeting_sessions(study_group_org_unit_id, learning_cycle_id, meeting_date);
CREATE INDEX idx_study_meeting_sessions_class_status
    ON study_meeting_sessions(class_org_unit_id, status, meeting_date);

CREATE TABLE IF NOT EXISTS study_meeting_attendances (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    study_meeting_session_id BIGINT NOT NULL,
    member_id BIGINT NOT NULL,
    home_study_group_org_unit_id VARCHAR(64) NOT NULL,
    attended_study_group_org_unit_id VARCHAR(64) NOT NULL,
    attendance_type VARCHAR(16) NOT NULL,
    added_by_member_id BIGINT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_study_meeting_attendance UNIQUE(study_meeting_session_id, member_id),
    CONSTRAINT chk_study_meeting_attendance_type CHECK(attendance_type IN ('HOME_GROUP', 'CROSS_GROUP')),
    CONSTRAINT fk_study_meeting_attendance_session FOREIGN KEY(study_meeting_session_id) REFERENCES study_meeting_sessions(id) ON DELETE CASCADE,
    CONSTRAINT fk_study_meeting_attendance_member FOREIGN KEY(member_id) REFERENCES members(id),
    CONSTRAINT fk_study_meeting_attendance_home_group FOREIGN KEY(home_study_group_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_study_meeting_attendance_attended_group FOREIGN KEY(attended_study_group_org_unit_id) REFERENCES org_units(id),
    CONSTRAINT fk_study_meeting_attendance_adder FOREIGN KEY(added_by_member_id) REFERENCES members(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_study_meeting_attendances_member
    ON study_meeting_attendances(member_id, created_at);
CREATE INDEX idx_study_meeting_attendances_session
    ON study_meeting_attendances(study_meeting_session_id, attendance_type);
