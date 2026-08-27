-- 0036: V1.2 MVP-B 小组学习会事实记录
-- 不复用 attendance，不推进班级学习周期，不写正式积分账本。

CREATE TABLE IF NOT EXISTS study_meeting_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_code TEXT NOT NULL UNIQUE,
    class_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    study_group_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    learning_cycle_id INTEGER NOT NULL REFERENCES class_learning_cycles(id),
    meeting_date TEXT NOT NULL,
    created_by_member_id INTEGER NOT NULL REFERENCES members(id),
    created_by_role TEXT NOT NULL
        CHECK(created_by_role IN ('GROUP_LEADER', 'CLASS_COUNSELOR')),
    has_course INTEGER NOT NULL DEFAULT 0 CHECK(has_course IN (0, 1)),
    course_key TEXT,
    course_name_snapshot TEXT,
    course_credit_snapshot INTEGER,
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK(status IN ('DRAFT', 'SUBMITTED', 'CANCELLED')),
    submitted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK((has_course=0 AND course_key IS NULL)
          OR (has_course=1 AND course_key IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_study_meeting_sessions_group_cycle
    ON study_meeting_sessions(study_group_org_unit_id, learning_cycle_id, meeting_date);
CREATE INDEX IF NOT EXISTS idx_study_meeting_sessions_class_status
    ON study_meeting_sessions(class_org_unit_id, status, meeting_date);

CREATE TABLE IF NOT EXISTS study_meeting_attendances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_meeting_session_id INTEGER NOT NULL REFERENCES study_meeting_sessions(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id),
    home_study_group_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    attended_study_group_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    attendance_type TEXT NOT NULL
        CHECK(attendance_type IN ('HOME_GROUP', 'CROSS_GROUP')),
    added_by_member_id INTEGER NOT NULL REFERENCES members(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(study_meeting_session_id, member_id)
);

CREATE INDEX IF NOT EXISTS idx_study_meeting_attendances_member
    ON study_meeting_attendances(member_id, created_at);
CREATE INDEX IF NOT EXISTS idx_study_meeting_attendances_session
    ON study_meeting_attendances(study_meeting_session_id, attendance_type);
