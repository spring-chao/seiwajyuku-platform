-- 0059: Explicit internal coordination records for renewal support.
-- These records are intentionally distinct from renewal_followups, which
-- remain facts about communication with the member whose renewal is due.
CREATE TABLE IF NOT EXISTS renewal_support_requests (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    renewal_cycle_id BIGINT NOT NULL,
    supporter_member_id BIGINT NULL,
    supporter_person_id VARCHAR(64) NULL,
    supporter_name_snapshot VARCHAR(255) NOT NULL,
    supporter_role VARCHAR(32) NOT NULL,
    supporter_org_unit_id VARCHAR(64) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'REQUESTED',
    requested_by BIGINT NOT NULL,
    requested_at DATETIME NOT NULL,
    feedback_summary TEXT NULL,
    next_action TEXT NULL,
    feedback_by BIGINT NULL,
    feedback_at DATETIME NULL,
    closed_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_renewal_support_request_role CHECK(supporter_role IN (
        'REFERRER', 'GROUP_LEADER', 'GROUP_COUNSELOR', 'CLASS_TEACHER',
        'DEPUTY_CLASS_TEACHER', 'CLASS_DEVELOPMENT', 'CENTER_DEVELOPMENT'
    )),
    CONSTRAINT chk_renewal_support_request_status CHECK(status IN (
        'REQUESTED', 'FEEDBACK_RECEIVED', 'CLOSED', 'CANCELLED'
    )),
    CONSTRAINT fk_renewal_support_request_cycle FOREIGN KEY(renewal_cycle_id)
        REFERENCES renewal_cycles(id),
    CONSTRAINT fk_renewal_support_request_member FOREIGN KEY(supporter_member_id)
        REFERENCES members(id),
    CONSTRAINT fk_renewal_support_request_person FOREIGN KEY(supporter_person_id)
        REFERENCES person_profiles(id),
    CONSTRAINT fk_renewal_support_request_org FOREIGN KEY(supporter_org_unit_id)
        REFERENCES org_units(id),
    CONSTRAINT fk_renewal_support_request_requested_by FOREIGN KEY(requested_by)
        REFERENCES app_users(id),
    CONSTRAINT fk_renewal_support_request_feedback_by FOREIGN KEY(feedback_by)
        REFERENCES app_users(id),
    INDEX idx_renewal_support_requests_cycle_status(renewal_cycle_id, status, requested_at),
    INDEX idx_renewal_support_requests_supporter(supporter_member_id, supporter_person_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
