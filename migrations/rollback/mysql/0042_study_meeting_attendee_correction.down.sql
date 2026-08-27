-- Refuse lossy downgrade after operator-created facts. MySQL DDL is not atomic.
CREATE TEMPORARY TABLE b212_no_operator_facts_guard (n INTEGER CHECK(n=0));
INSERT INTO b212_no_operator_facts_guard SELECT COUNT(*) FROM study_meeting_attendances WHERE added_by_user_id IS NOT NULL;
DROP TEMPORARY TABLE b212_no_operator_facts_guard;
ALTER TABLE study_meeting_attendances
    DROP CHECK chk_meeting_attendance_actor,
    DROP FOREIGN KEY fk_study_meeting_attendance_operator,
    DROP COLUMN added_by_user_id,
    MODIFY COLUMN added_by_member_id BIGINT NOT NULL;
DELETE FROM role_permissions WHERE permission_key='study_meetings:attendees_edit';
DELETE FROM permissions WHERE permission_key='study_meetings:attendees_edit';
DELETE FROM schema_migrations WHERE version='0042_study_meeting_attendee_correction.sql';
