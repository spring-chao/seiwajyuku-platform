-- 0047: C6 班级学习会学分投影
-- 班级学习会的分值来自 attendance_score_records；本迁移只登记
-- 可用于未来正式入账的规则，不会创建任何学分账本记录。

INSERT OR IGNORE INTO learning_credit_rules
    (rule_version_id, rule_key, credit_category, credit_type, settlement_model,
     points, cap_points, rule_snapshot_json, created_at, updated_at)
SELECT id, 'CLASS_MEETING_SCORE', 'STANDARD_LEARNING', 'CLASS_MEETING_SCORE', 'EVENT_ONCE',
       NULL, NULL,
       '{"points":"FROM_ATTENDANCE_SCORE","unit":"PERSON_MEETING","source":"attendance_score_records"}',
       datetime('now'), datetime('now')
FROM learning_credit_rule_versions
WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1';
