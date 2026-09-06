-- Refuse a lossy downgrade after a class-meeting credit has been posted.
BEGIN;
CREATE TEMP TABLE c6_no_class_meeting_credit_guard (n INTEGER CHECK(n=0));
INSERT INTO c6_no_class_meeting_credit_guard
    SELECT COUNT(*) FROM learning_credit_entries
    WHERE rule_key='CLASS_MEETING_SCORE';
DROP TABLE c6_no_class_meeting_credit_guard;

DELETE FROM learning_credit_rules
WHERE rule_key='CLASS_MEETING_SCORE'
  AND rule_version_id=(
      SELECT id FROM learning_credit_rule_versions
      WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1'
  );
DELETE FROM schema_migrations WHERE version='0047_class_meeting_credit_projection.sql';
COMMIT;
