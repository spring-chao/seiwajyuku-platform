-- 0008: member_org_relations - multi-relation model for class/group/special cohort
-- Enables班主任 to see only their class, 组长 to see only their group,
-- and黄埔班 managers to see only special cohort members.

CREATE TABLE IF NOT EXISTS member_org_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id),
    org_unit_id VARCHAR(64) NOT NULL REFERENCES org_units(id),
    relation_type VARCHAR(32) NOT NULL CHECK(relation_type IN (
        'PRIMARY_REGION', 'STUDY_CLASS', 'STUDY_GROUP',
        'SPECIAL_COHORT', 'DEVELOPMENT_RELATION'
    )),
    is_primary INTEGER NOT NULL DEFAULT 0,
    valid_from TEXT,
    valid_until TEXT,
    source_type VARCHAR(32) NOT NULL DEFAULT 'MANUAL',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_member_org_relations_member ON member_org_relations(member_id);
CREATE INDEX IF NOT EXISTS idx_member_org_relations_org ON member_org_relations(org_unit_id);
CREATE INDEX IF NOT EXISTS idx_member_org_relations_type ON member_org_relations(relation_type);
CREATE UNIQUE INDEX IF NOT EXISTS uq_member_org_relation
    ON member_org_relations(member_id, org_unit_id, relation_type);
