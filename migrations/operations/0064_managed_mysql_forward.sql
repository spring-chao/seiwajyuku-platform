-- Fixed managed MySQL 0064 forward operation. Exact guards are checked under the application advisory lock before this file runs.
-- No temporary table privilege, arbitrary SQL, or automatic replay is used.
START TRANSACTION;

CREATE TABLE IF NOT EXISTS g5_4_c0_rule_mapping_state (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    target_plan_key VARCHAR(128) NOT NULL,
    target_plan_version_label VARCHAR(64) NOT NULL,
    original_course_status VARCHAR(32) NOT NULL,
    original_mapping_id BIGINT NULL,
    original_mapping_status VARCHAR(32) NULL,
    original_generic_rule_version_id BIGINT NULL,
    original_course_credit_rule_version_id BIGINT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_g5_4_c0_state UNIQUE(target_plan_key, target_plan_version_label)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT IGNORE INTO g5_4_c0_rule_mapping_state
    (target_plan_key, target_plan_version_label, original_course_status,
     original_mapping_id, original_mapping_status,
     original_generic_rule_version_id, original_course_credit_rule_version_id, created_at)
SELECT 'standard-3y', '2026', v.status, m.id, m.status,
       m.generic_rule_version_id, m.course_credit_rule_version_id, UTC_TIMESTAMP()
FROM learning_plan_credit_rule_versions v
LEFT JOIN learning_plan_credit_rule_mappings m
  ON m.plan_key='standard-3y' AND m.plan_version_label='2026'
WHERE v.plan_key='STANDARD_3Y_2026' AND v.version_label='2026.1';

UPDATE learning_plan_credit_rule_versions
SET status='PUBLISHED', updated_at=UTC_TIMESTAMP()
WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1';

INSERT INTO learning_plan_credit_rule_mappings
    (plan_key, plan_version_label, generic_rule_version_id,
     course_credit_rule_version_id, status, mapping_source, created_at, updated_at)
SELECT 'standard-3y', '2026', generic.id, course.id, 'ACTIVE', 'G5.4-C0',
       UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM learning_credit_rule_versions generic
JOIN learning_plan_credit_rule_versions course
  ON course.plan_key='STANDARD_3Y_2026' AND course.version_label='2026.1'
WHERE generic.rule_set_key='STANDARD_3Y_2026'
  AND generic.version_label='2026.1'
  AND generic.status='PUBLISHED'
  AND course.status='PUBLISHED'
  AND NOT EXISTS (
      SELECT 1 FROM learning_plan_credit_rule_mappings existing
      WHERE existing.plan_key='standard-3y' AND existing.plan_version_label='2026'
  );

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

COMMIT;
