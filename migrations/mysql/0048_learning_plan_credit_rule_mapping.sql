-- 0048: G5.3 explicit learning-plan to credit-policy mapping.
-- This migration creates no learning-credit ledger entries.

ALTER TABLE class_learning_bindings
    ADD COLUMN course_credit_rule_version_id BIGINT NULL,
    ADD CONSTRAINT fk_class_learning_binding_course_credit_rule
        FOREIGN KEY(course_credit_rule_version_id)
        REFERENCES learning_plan_credit_rule_versions(id);
CREATE INDEX idx_class_learning_bindings_course_credit_rule
    ON class_learning_bindings(course_credit_rule_version_id);

CREATE TABLE IF NOT EXISTS learning_plan_credit_rule_mappings (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    plan_key VARCHAR(128) NOT NULL,
    plan_version_label VARCHAR(64) NOT NULL,
    generic_rule_version_id BIGINT NOT NULL,
    course_credit_rule_version_id BIGINT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    mapping_source VARCHAR(64) NOT NULL DEFAULT 'G5.3',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_learning_plan_credit_mapping_status CHECK(status IN ('ACTIVE', 'RETIRED')),
    CONSTRAINT uq_learning_plan_credit_mapping UNIQUE(plan_key, plan_version_label),
    CONSTRAINT fk_learning_plan_credit_mapping_generic
        FOREIGN KEY(generic_rule_version_id) REFERENCES learning_credit_rule_versions(id),
    CONSTRAINT fk_learning_plan_credit_mapping_course
        FOREIGN KEY(course_credit_rule_version_id) REFERENCES learning_plan_credit_rule_versions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX idx_learning_plan_credit_mapping_status
    ON learning_plan_credit_rule_mappings(plan_key, plan_version_label, status);

INSERT IGNORE INTO learning_plan_credit_rule_versions
    (plan_key, version_label, status, based_on_version_label, created_at, updated_at)
VALUES ('STANDARD_3Y_2026', '2026.1', 'PUBLISHED', '2026', UTC_TIMESTAMP(), UTC_TIMESTAMP());

INSERT IGNORE INTO learning_plan_credit_rule_mappings
    (plan_key, plan_version_label, generic_rule_version_id,
     course_credit_rule_version_id, status, mapping_source, created_at, updated_at)
SELECT 'standard-3y', '2026', generic.id, course.id, 'ACTIVE', 'G5.3',
       UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM learning_credit_rule_versions generic
JOIN learning_plan_credit_rule_versions course
  ON course.plan_key='STANDARD_3Y_2026' AND course.version_label='2026.1'
WHERE generic.rule_set_key='STANDARD_3Y_2026'
  AND generic.version_label='2026.1'
  AND generic.status='PUBLISHED'
  AND course.status='PUBLISHED';

UPDATE class_learning_bindings b
JOIN learning_plan_versions p ON p.id=b.plan_version_id
JOIN learning_plan_credit_rule_mappings m
  ON m.plan_key=p.plan_key AND m.plan_version_label=p.version_label
 AND m.plan_key='standard-3y' AND m.plan_version_label='2026'
 AND m.status='ACTIVE'
SET b.credit_rule_version_id=COALESCE(b.credit_rule_version_id, m.generic_rule_version_id),
    b.course_credit_rule_version_id=COALESCE(b.course_credit_rule_version_id, m.course_credit_rule_version_id);

UPDATE study_meeting_courses c
JOIN study_meeting_sessions s ON s.id=c.study_meeting_session_id
JOIN class_learning_cycles lc ON lc.id=s.learning_cycle_id
JOIN class_learning_bindings b ON b.id=lc.binding_id
JOIN learning_plan_versions p ON p.id=b.plan_version_id
SET c.credit_rule_version_id=b.course_credit_rule_version_id
WHERE p.plan_key='standard-3y' AND p.version_label='2026'
  AND b.course_credit_rule_version_id IS NOT NULL
  AND c.credit_rule_version_id IS NULL;
