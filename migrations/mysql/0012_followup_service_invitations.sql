CREATE TABLE IF NOT EXISTS followup_service_invitations (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    task_id BIGINT NOT NULL,
    invitation_type VARCHAR(16) NOT NULL,
    invited_user_id BIGINT NOT NULL,
    invited_by_user_id BIGINT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    invitation_message TEXT NULL,
    proposed_due_at DATETIME NULL,
    requested_due_at DATETIME NULL,
    response_note TEXT NULL,
    valid_until DATETIME NOT NULL,
    responded_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    INDEX idx_followup_invitations_task(task_id, invitation_type, status),
    INDEX idx_followup_invitations_invitee(invited_user_id, status, valid_until),
    FOREIGN KEY(task_id) REFERENCES followup_tasks(id) ON DELETE CASCADE,
    FOREIGN KEY(invited_user_id) REFERENCES app_users(id),
    FOREIGN KEY(invited_by_user_id) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS followup_collaborators (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    task_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    invitation_id BIGINT NULL,
    collaboration_role VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    starts_at DATETIME NOT NULL,
    ends_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_followup_collaborator(task_id, user_id, collaboration_role),
    INDEX idx_followup_collaborators_user(user_id, status, task_id),
    FOREIGN KEY(task_id) REFERENCES followup_tasks(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES app_users(id),
    FOREIGN KEY(invitation_id) REFERENCES followup_service_invitations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
