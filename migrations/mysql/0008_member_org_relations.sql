-- 0008: member_org_relations - multi-relation model for class/group/special cohort
-- Enables班主任 to see only their class, 组长 to see only their group,
-- and黄埔班 managers to see only special cohort members.

CREATE TABLE IF NOT EXISTS member_org_relations (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    member_id BIGINT NOT NULL,
    org_unit_id VARCHAR(64) NOT NULL,
    relation_type VARCHAR(32) NOT NULL,
    is_primary TINYINT NOT NULL DEFAULT 0,
    valid_from DATE NULL,
    valid_until DATE NULL,
    source_type VARCHAR(32) NOT NULL DEFAULT 'MANUAL',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_mor_member FOREIGN KEY (member_id) REFERENCES members(id),
    CONSTRAINT fk_mor_org FOREIGN KEY (org_unit_id) REFERENCES org_units(id),
    CONSTRAINT chk_mor_relation_type CHECK(relation_type IN (
        'PRIMARY_REGION', 'STUDY_CLASS', 'STUDY_GROUP',
        'SPECIAL_COHORT', 'DEVELOPMENT_RELATION'
    ))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_member_org_relations_member ON member_org_relations(member_id);
CREATE INDEX idx_member_org_relations_org ON member_org_relations(org_unit_id);
CREATE INDEX idx_member_org_relations_type ON member_org_relations(relation_type);
CREATE UNIQUE INDEX uq_member_org_relation
    ON member_org_relations(member_id, org_unit_id, relation_type);
