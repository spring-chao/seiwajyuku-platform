-- Refuse a lossy downgrade once the ledger or completion facts have been used.
BEGIN;
CREATE TEMP TABLE g5_no_credit_facts_guard (n INTEGER CHECK(n=0));
INSERT INTO g5_no_credit_facts_guard SELECT COUNT(*) FROM learning_credit_entries;
INSERT INTO g5_no_credit_facts_guard SELECT COUNT(*) FROM study_meeting_course_completions;
DROP TABLE g5_no_credit_facts_guard;

DROP TABLE study_meeting_course_completions;
DROP INDEX IF EXISTS idx_study_meeting_course_completions_member;
ALTER TABLE study_meeting_courses DROP COLUMN plan_version_label;
ALTER TABLE study_meeting_courses DROP COLUMN plan_key;
ALTER TABLE study_meeting_courses DROP COLUMN credit_rule_version_id;
ALTER TABLE study_meeting_courses DROP COLUMN completion_note;
ALTER TABLE study_meeting_courses DROP COLUMN confirmed_by_user_id;
ALTER TABLE study_meeting_courses DROP COLUMN confirmed_by_member_id;
ALTER TABLE study_meeting_courses DROP COLUMN completed_at;
ALTER TABLE study_meeting_courses DROP COLUMN completion_status;
DROP INDEX IF EXISTS idx_class_learning_bindings_credit_rule;
ALTER TABLE class_learning_bindings DROP COLUMN credit_rule_version_id;

DROP TABLE learning_credit_entries;
DROP TABLE learning_credit_rules;
DROP TABLE learning_credit_rule_versions;
DELETE FROM role_permissions WHERE permission_key IN ('plans:credit_settlement_preview', 'plans:credit_settlement_manage');
DELETE FROM permissions WHERE permission_key IN ('plans:credit_settlement_preview', 'plans:credit_settlement_manage');
DELETE FROM schema_migrations WHERE version='0043_learning_credit_ledger.sql';
COMMIT;
