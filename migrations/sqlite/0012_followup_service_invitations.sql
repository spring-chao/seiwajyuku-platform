-- 0012: invitation-based volunteer service collaboration.
-- Legacy followup tasks remain valid without invitation records.

CREATE TABLE IF NOT EXISTS followup_service_invitations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES followup_tasks(id) ON DELETE CASCADE,
    invitation_type TEXT NOT NULL CHECK (invitation_type IN ('ASSIGNEE', 'COMPANION')),
    invited_user_id INTEGER NOT NULL REFERENCES app_users(id),
    invited_by_user_id INTEGER NOT NULL REFERENCES app_users(id),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN (
        'PENDING', 'ACCEPTED', 'ADJUSTMENT_REQUESTED',
        'UNAVAILABLE', 'CANCELLED', 'EXPIRED'
    )),
    invitation_message TEXT,
    proposed_due_at TEXT,
    requested_due_at TEXT,
    response_note TEXT,
    valid_until TEXT NOT NULL,
    responded_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_followup_invitations_task
    ON followup_service_invitations(task_id, invitation_type, status);
CREATE INDEX IF NOT EXISTS idx_followup_invitations_invitee
    ON followup_service_invitations(invited_user_id, status, valid_until);

CREATE TABLE IF NOT EXISTS followup_collaborators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES followup_tasks(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES app_users(id),
    invitation_id INTEGER REFERENCES followup_service_invitations(id),
    collaboration_role TEXT NOT NULL CHECK (collaboration_role IN ('ASSIGNEE', 'COMPANION')),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'REMOVED')),
    starts_at TEXT NOT NULL,
    ends_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(task_id, user_id, collaboration_role)
);
CREATE INDEX IF NOT EXISTS idx_followup_collaborators_user
    ON followup_collaborators(user_id, status, task_id);
