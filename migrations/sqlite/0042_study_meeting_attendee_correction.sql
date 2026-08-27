-- Preserve original adders; operator-added facts must not impersonate a member.
BEGIN;
CREATE TABLE study_meeting_attendances_b212 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_meeting_session_id INTEGER NOT NULL REFERENCES study_meeting_sessions(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id),
    home_study_group_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    attended_study_group_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    attendance_type TEXT NOT NULL CHECK(attendance_type IN ('HOME_GROUP', 'CROSS_GROUP')),
    added_by_member_id INTEGER NULL REFERENCES members(id),
    added_by_user_id INTEGER NULL REFERENCES app_users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(study_meeting_session_id, member_id),
    CONSTRAINT chk_meeting_attendance_actor CHECK(
      (added_by_member_id IS NOT NULL AND added_by_user_id IS NULL)
      OR (added_by_member_id IS NULL AND added_by_user_id IS NOT NULL))
);
INSERT INTO study_meeting_attendances_b212
  (id, study_meeting_session_id, member_id, home_study_group_org_unit_id,
   attended_study_group_org_unit_id, attendance_type, added_by_member_id, created_at, updated_at)
SELECT id, study_meeting_session_id, member_id, home_study_group_org_unit_id,
   attended_study_group_org_unit_id, attendance_type, added_by_member_id, created_at, updated_at
FROM study_meeting_attendances;
DROP TABLE study_meeting_attendances;
ALTER TABLE study_meeting_attendances_b212 RENAME TO study_meeting_attendances;
CREATE INDEX idx_study_meeting_attendances_member ON study_meeting_attendances(member_id, created_at);
CREATE INDEX idx_study_meeting_attendances_session ON study_meeting_attendances(study_meeting_session_id, attendance_type);
INSERT OR IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('study_meetings:attendees_edit', '修正已提交学习会参加人员', 'SENSITIVE', datetime('now'));
INSERT OR IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'study_meetings:attendees_edit' FROM roles WHERE role_key IN ('system_admin', 'operations_admin');
COMMIT;
