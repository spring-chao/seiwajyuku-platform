-- 0064 rollback is loss-averse.  It can only undo an unused C0 promotion.
-- Any frozen binding, course fact, activity fact, or ledger reference requires
-- snapshot recovery or an approved forward correction instead.
CREATE TEMPORARY TABLE g5_4_c0_rollback_guard (n INT CHECK(n=0));
INSERT INTO g5_4_c0_rollback_guard
SELECT COUNT(*)
FROM g5_4_c0_rule_mapping_state s
JOIN learning_plan_credit_rule_versions v
  ON v.plan_key='STANDARD_3Y_2026' AND v.version_label='2026.1'
WHERE v.status<>'PUBLISHED';
INSERT INTO g5_4_c0_rollback_guard
SELECT COUNT(*)
FROM learning_plan_credit_rule_mappings m
JOIN g5_4_c0_rule_mapping_state s
  ON s.target_plan_key=m.plan_key AND s.target_plan_version_label=m.plan_version_label
WHERE NOT (
    m.status='ACTIVE'
    AND m.generic_rule_version_id=(SELECT id FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1')
    AND m.course_credit_rule_version_id=(SELECT id FROM learning_plan_credit_rule_versions WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1')
);
INSERT INTO g5_4_c0_rollback_guard
SELECT COUNT(*)
FROM class_learning_bindings b
JOIN g5_4_c0_rule_mapping_state s
WHERE b.credit_rule_version_id=(SELECT id FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1')
   OR b.course_credit_rule_version_id=(SELECT id FROM learning_plan_credit_rule_versions WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1');
INSERT INTO g5_4_c0_rollback_guard
SELECT COUNT(*)
FROM study_meeting_courses c
WHERE c.credit_rule_version_id=(SELECT id FROM learning_plan_credit_rule_versions WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1');
INSERT INTO g5_4_c0_rollback_guard
SELECT COUNT(*)
FROM learning_credit_activity_facts f
JOIN class_learning_bindings b ON b.id=f.binding_id
WHERE b.credit_rule_version_id=(SELECT id FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1')
   OR b.course_credit_rule_version_id=(SELECT id FROM learning_plan_credit_rule_versions WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1');
INSERT INTO g5_4_c0_rollback_guard
SELECT COUNT(*)
FROM learning_credit_entries e
LEFT JOIN class_learning_cycles lc ON lc.id=e.learning_cycle_id
LEFT JOIN class_learning_bindings b ON b.id=lc.binding_id
WHERE e.rule_version_id IN (
    (SELECT id FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1'),
    (SELECT id FROM learning_plan_credit_rule_versions WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1')
)
   OR b.credit_rule_version_id=(SELECT id FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1')
   OR b.course_credit_rule_version_id=(SELECT id FROM learning_plan_credit_rule_versions WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1');
DROP TEMPORARY TABLE g5_4_c0_rollback_guard;

DELETE FROM learning_plan_credit_rule_mappings
WHERE plan_key='standard-3y' AND plan_version_label='2026'
  AND NOT EXISTS (
      SELECT 1 FROM g5_4_c0_rule_mapping_state s
      WHERE s.original_mapping_id IS NOT NULL
  );
UPDATE learning_plan_credit_rule_versions v
JOIN g5_4_c0_rule_mapping_state s
  ON s.original_course_status='DRAFT'
SET v.status='DRAFT', v.updated_at=UTC_TIMESTAMP()
WHERE v.plan_key='STANDARD_3Y_2026' AND v.version_label='2026.1';
DROP TABLE g5_4_c0_rule_mapping_state;
DELETE FROM schema_migrations WHERE version='0064_fix_credit_rule_mapping_and_binding_freeze.sql';
