-- 0073: Monthly study-meeting attribution only; never updates source facts or credits.
CREATE TABLE IF NOT EXISTS study_meeting_roster_months (
 period CHAR(7) PRIMARY KEY, freeze_due_at DATETIME NOT NULL,
 state VARCHAR(16) NOT NULL DEFAULT 'OPEN', frozen_at DATETIME NULL,
 created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
 CONSTRAINT chk_sm_roster_month_state CHECK (state IN ('OPEN','FROZEN'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS study_meeting_roster_members (
 period CHAR(7) NOT NULL, member_id BIGINT NOT NULL,
 class_org_unit_id VARCHAR(64) NOT NULL, group_org_unit_id VARCHAR(64) NULL,
 class_name VARCHAR(255) NOT NULL, group_name VARCHAR(255) NULL,
 included TINYINT NOT NULL DEFAULT 1, cross_class_preserved TINYINT NOT NULL DEFAULT 0,
 updated_at DATETIME NOT NULL, PRIMARY KEY (period,member_id),
 INDEX idx_sm_roster_members_class (period,class_org_unit_id,group_org_unit_id),
 CONSTRAINT fk_sm_roster_member_month FOREIGN KEY (period) REFERENCES study_meeting_roster_months(period),
 CONSTRAINT fk_sm_roster_member_id FOREIGN KEY (member_id) REFERENCES members(id),
 CONSTRAINT fk_sm_roster_member_class FOREIGN KEY (class_org_unit_id) REFERENCES org_units(id),
 CONSTRAINT fk_sm_roster_member_group FOREIGN KEY (group_org_unit_id) REFERENCES org_units(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS study_meeting_roster_groups (
 period CHAR(7) NOT NULL, group_org_unit_id VARCHAR(64) NOT NULL,
 class_org_unit_id VARCHAR(64) NOT NULL, class_name VARCHAR(255) NOT NULL,
 group_name VARCHAR(255) NOT NULL, PRIMARY KEY (period,group_org_unit_id),
 CONSTRAINT fk_sm_roster_group_month FOREIGN KEY (period) REFERENCES study_meeting_roster_months(period),
 CONSTRAINT fk_sm_roster_group_id FOREIGN KEY (group_org_unit_id) REFERENCES org_units(id),
 CONSTRAINT fk_sm_roster_group_class FOREIGN KEY (class_org_unit_id) REFERENCES org_units(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
