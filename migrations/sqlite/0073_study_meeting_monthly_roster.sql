-- 0073: Append-only frozen monthly attribution, separate from meeting and credit facts.
CREATE TABLE IF NOT EXISTS study_meeting_roster_months (
 period TEXT PRIMARY KEY, freeze_due_at TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN','FROZEN')),
 frozen_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS study_meeting_roster_members (
 period TEXT NOT NULL REFERENCES study_meeting_roster_months(period),
 member_id INTEGER NOT NULL REFERENCES members(id),
 class_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
 group_org_unit_id TEXT REFERENCES org_units(id),
 class_name TEXT NOT NULL, group_name TEXT,
 included INTEGER NOT NULL DEFAULT 1, cross_class_preserved INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL, PRIMARY KEY (period,member_id)
);
CREATE INDEX IF NOT EXISTS idx_sm_roster_members_class ON study_meeting_roster_members(period,class_org_unit_id,group_org_unit_id);
CREATE TABLE IF NOT EXISTS study_meeting_roster_groups (
 period TEXT NOT NULL REFERENCES study_meeting_roster_months(period),
 group_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
 class_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
 class_name TEXT NOT NULL, group_name TEXT NOT NULL, PRIMARY KEY (period,group_org_unit_id)
);
