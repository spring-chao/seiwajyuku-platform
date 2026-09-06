-- Refuse a lossy downgrade once a binding or course fact uses the explicit
-- course-policy reference introduced by G5.3.
BEGIN;
CREATE TEMP TABLE g5_3_mapping_guard (n INTEGER CHECK(n=0));
INSERT INTO g5_3_mapping_guard
SELECT COUNT(*)
FROM class_learning_bindings
WHERE course_credit_rule_version_id IS NOT NULL;
INSERT INTO g5_3_mapping_guard
SELECT COUNT(*)
FROM study_meeting_courses
WHERE credit_rule_version_id IS NOT NULL;
DROP TABLE g5_3_mapping_guard;

DELETE FROM learning_plan_credit_rule_mappings
WHERE plan_key='standard-3y' AND plan_version_label='2026';
DROP INDEX IF EXISTS idx_learning_plan_credit_rule_mappings_status;
DROP INDEX IF EXISTS idx_class_learning_bindings_course_credit_rule;
ALTER TABLE class_learning_bindings DROP COLUMN course_credit_rule_version_id;
DROP TABLE learning_plan_credit_rule_mappings;
DELETE FROM schema_migrations WHERE version='0048_learning_plan_credit_rule_mapping.sql';
COMMIT;
