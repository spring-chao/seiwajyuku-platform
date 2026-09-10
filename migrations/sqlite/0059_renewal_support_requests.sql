-- 0059: Explicit internal coordination records for renewal support.
-- These records are intentionally distinct from renewal_followups, which
-- remain facts about communication with the member whose renewal is due.
CREATE TABLE IF NOT EXISTS renewal_support_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    renewal_cycle_id INTEGER NOT NULL REFERENCES renewal_cycles(id),
    supporter_member_id INTEGER REFERENCES members(id),
    supporter_person_id TEXT REFERENCES person_profiles(id),
    supporter_name_snapshot TEXT NOT NULL,
    supporter_role TEXT NOT NULL CHECK(supporter_role IN (
        'REFERRER', 'GROUP_LEADER', 'GROUP_COUNSELOR', 'CLASS_TEACHER',
        'DEPUTY_CLASS_TEACHER', 'CLASS_DEVELOPMENT', 'CENTER_DEVELOPMENT'
    )),
    supporter_org_unit_id TEXT REFERENCES org_units(id),
    status TEXT NOT NULL DEFAULT 'REQUESTED' CHECK(status IN (
        'REQUESTED', 'FEEDBACK_RECEIVED', 'CLOSED', 'CANCELLED'
    )),
    requested_by INTEGER NOT NULL REFERENCES app_users(id),
    requested_at TEXT NOT NULL,
    feedback_summary TEXT,
    next_action TEXT,
    feedback_by INTEGER REFERENCES app_users(id),
    feedback_at TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(length(trim(supporter_name_snapshot)) > 0)
);
CREATE INDEX IF NOT EXISTS idx_renewal_support_requests_cycle_status
    ON renewal_support_requests(renewal_cycle_id, status, requested_at);
CREATE INDEX IF NOT EXISTS idx_renewal_support_requests_supporter
    ON renewal_support_requests(supporter_member_id, supporter_person_id, status);
