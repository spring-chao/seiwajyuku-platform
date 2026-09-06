-- Refuse a lossy downgrade once the ledger or completion facts have been used.
CREATE TEMPORARY TABLE g5_no_credit_facts_guard (n INTEGER CHECK(n=0));
INSERT INTO g5_no_credit_facts_guard SELECT COUNT(*) FROM learning_credit_entries;
INSERT INTO g5_no_credit_facts_guard SELECT COUNT(*) FROM study_meeting_course_completions;
DROP TEMPORARY TABLE g5_no_credit_facts_guard;

DROP TABLE study_meeting_course_completions;
ALTER TABLE study_meeting_courses
    DROP COLUMN plan_version_label,
    DROP COLUMN plan_key,
    DROP COLUMN credit_rule_version_id,
    DROP COLUMN completion_note,
    DROP COLUMN confirmed_by_user_id,
    DROP COLUMN confirmed_by_member_id,
    DROP COLUMN completed_at,
    DROP COLUMN completion_status;
ALTER TABLE class_learning_bindings DROP COLUMN credit_rule_version_id;
DROP TABLE learning_credit_entries;
DROP TABLE learning_credit_rules;
DROP TABLE learning_credit_rule_versions;
DELETE FROM role_permissions WHERE permission_key IN ('plans:credit_settlement_preview', 'plans:credit_settlement_manage');
DELETE FROM permissions WHERE permission_key IN ('plans:credit_settlement_preview', 'plans:credit_settlement_manage');
DELETE FROM schema_migrations WHERE version='0046_learning_credit_ledger.sql';
