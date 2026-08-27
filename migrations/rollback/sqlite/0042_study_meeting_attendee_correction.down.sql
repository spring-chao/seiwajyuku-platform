-- Refuse lossy downgrade after operator-created facts; retain additive schema instead.
BEGIN;
CREATE TEMP TABLE b212_no_operator_facts_guard (n INTEGER CHECK(n=0));
INSERT INTO b212_no_operator_facts_guard SELECT COUNT(*) FROM study_meeting_attendances WHERE added_by_user_id IS NOT NULL;
DROP TABLE b212_no_operator_facts_guard;
CREATE TABLE study_meeting_attendances_pre_b212 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_meeting_session_id INTEGER NOT NULL REFERENCES study_meeting_sessions(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id),
    home_study_group_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    attended_study_group_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    attendance_type TEXT NOT NULL CHECK(attendance_type IN ('HOME_GROUP', 'CROSS_GROUP')),
    added_by_member_id INTEGER NOT NULL REFERENCES members(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(study_meeting_session_id, member_id)
);
INSERT INTO study_meeting_attendances_pre_b212 SELECT id, study_meeting_session_id, member_id,
 home_study_group_org_unit_id, attended_study_group_org_unit_id, attendance_type, added_by_member_id, created_at, updated_at
FROM study_meeting_attendances;
DROP TABLE study_meeting_attendances;
ALTER TABLE study_meeting_attendances_pre_b212 RENAME TO study_meeting_attendances;
CREATE INDEX idx_study_meeting_attendances_member ON study_meeting_attendances(member_id, created_at);
CREATE INDEX idx_study_meeting_attendances_session ON study_meeting_attendances(study_meeting_session_id, attendance_type);
DELETE FROM role_permissions WHERE permission_key='study_meetings:attendees_edit';
DELETE FROM permissions WHERE permission_key='study_meetings:attendees_edit';
DELETE FROM schema_migrations WHERE version='0042_study_meeting_attendee_correction.sql';
COMMIT;
