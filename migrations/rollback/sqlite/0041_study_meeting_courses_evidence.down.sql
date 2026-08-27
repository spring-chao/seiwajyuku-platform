-- Destructive rollback: export new course/evidence metadata and clear objects first.
-- Prefer application rollback retaining this additive schema.
DELETE FROM role_permissions WHERE permission_key='study_meetings:courses_edit';
DELETE FROM permissions WHERE permission_key='study_meetings:courses_edit';
DROP TABLE study_meeting_evidence;
DROP TABLE study_meeting_courses;
ALTER TABLE study_meeting_sessions DROP COLUMN course_details_initialized;
DELETE FROM schema_migrations WHERE version='0041_study_meeting_courses_evidence.sql';
