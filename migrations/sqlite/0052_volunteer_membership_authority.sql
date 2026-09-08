-- 0052: 志工授权以正式学长在册状态和当前岗位为准。
-- member_id 是志工岗位的正式业务主体。无法唯一回填的旧记录保留为历史，
-- 但不会被新的权限读取路径当作当前授权。
-- starts_at / ends_at 仅保留为旧记录及系统确认、结束操作的审计时间，
-- 不再表示普通志工岗位的业务任期或自动到期条件。

PRAGMA foreign_keys=OFF;
BEGIN;

ALTER TABLE volunteer_appointments RENAME TO volunteer_appointments_0052_legacy;

CREATE TABLE volunteer_appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person_profiles(id),
    member_id INTEGER REFERENCES members(id),
    appointment_key TEXT NOT NULL,
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('SUBTREE', 'UNIT')),
    starts_at TEXT NOT NULL,
    ends_at TEXT,
    status TEXT NOT NULL DEFAULT 'PLANNED' CHECK (status IN (
        'PLANNED', 'ACTIVE', 'SUSPENDED', 'ENDED', 'REVOKED'
    )),
    source_reference TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO volunteer_appointments
    (id, person_id, member_id, appointment_key, org_unit_id, scope_type,
     starts_at, ends_at, status, source_reference, created_at, updated_at)
SELECT legacy.id,
       legacy.person_id,
       CASE WHEN (
           SELECT COUNT(*) FROM member_identities mi
           WHERE mi.person_id=legacy.person_id
       )=1 THEN (
           SELECT mi.member_id FROM member_identities mi
           WHERE mi.person_id=legacy.person_id
           LIMIT 1
       ) ELSE NULL END,
       legacy.appointment_key,
       legacy.org_unit_id,
       legacy.scope_type,
       legacy.starts_at,
       legacy.ends_at,
       legacy.status,
       legacy.source_reference,
       legacy.created_at,
       legacy.updated_at
FROM volunteer_appointments_0052_legacy legacy;

DROP TABLE volunteer_appointments_0052_legacy;

CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_person
    ON volunteer_appointments(person_id, status, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_member
    ON volunteer_appointments(member_id, status);
CREATE INDEX IF NOT EXISTS idx_volunteer_appointments_org
    ON volunteer_appointments(org_unit_id, status, starts_at, ends_at);

COMMIT;
PRAGMA foreign_keys=ON;
