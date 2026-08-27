-- No organization, evidence, course, learning-cycle or credit writes.
ALTER TABLE study_meeting_attendances
    MODIFY COLUMN added_by_member_id BIGINT NULL,
    ADD COLUMN added_by_user_id BIGINT NULL,
    ADD CONSTRAINT fk_study_meeting_attendance_operator FOREIGN KEY(added_by_user_id) REFERENCES app_users(id),
    ADD CONSTRAINT chk_meeting_attendance_actor CHECK(
      (added_by_member_id IS NOT NULL AND added_by_user_id IS NULL)
      OR (added_by_member_id IS NULL AND added_by_user_id IS NOT NULL));
INSERT IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at)
VALUES ('study_meetings:attendees_edit', '修正已提交学习会参加人员', 'SENSITIVE', UTC_TIMESTAMP());
INSERT IGNORE INTO role_permissions(role_key, permission_key)
SELECT role_key, 'study_meetings:attendees_edit' FROM roles WHERE role_key IN ('system_admin', 'operations_admin');
