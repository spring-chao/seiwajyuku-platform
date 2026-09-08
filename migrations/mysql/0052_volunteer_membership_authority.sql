-- 0052: 志工授权以正式学长在册状态和当前岗位为准。
-- member_id 无法唯一回填的旧记录保留为历史，不能被视为当前志工授权。
-- starts_at / ends_at 仅是旧记录和系统审计时间，不再作为普通志工岗位的任期条件。

ALTER TABLE volunteer_appointments
    ADD COLUMN member_id BIGINT NULL AFTER person_id,
    ADD INDEX idx_volunteer_appointments_member (member_id, status);

UPDATE volunteer_appointments va
JOIN (
    SELECT person_id, MIN(member_id) AS member_id
    FROM member_identities
    GROUP BY person_id
    HAVING COUNT(*)=1
) mi ON mi.person_id=va.person_id
SET va.member_id=mi.member_id
WHERE va.member_id IS NULL;

ALTER TABLE volunteer_appointments
    ADD CONSTRAINT fk_volunteer_appointments_member
    FOREIGN KEY(member_id) REFERENCES members(id);
