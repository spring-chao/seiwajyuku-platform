-- User-confirmed production merge (2026-08-15), verified read-only first.
-- Canonical: 3efcbf8d-c992-4f57-a09d-8b8cfa4cd134 (five child groups).
-- Source: org-wuyue-3 (no child group, 29 active members, one active event).
-- MySQL DDL commits implicitly. A failed startup can therefore leave this
-- empty guard table behind even though all data changes were rolled back.
DROP TABLE IF EXISTS migration_guard_0024_merge_duplicate_wuyue_three;

CREATE TABLE migration_guard_0024_merge_duplicate_wuyue_three (
    ok TINYINT NOT NULL CHECK (ok = 1)
);

INSERT INTO migration_guard_0024_merge_duplicate_wuyue_three(ok)
SELECT CASE
    WHEN NOT EXISTS (SELECT 1 FROM org_units WHERE id='3efcbf8d-c992-4f57-a09d-8b8cfa4cd134') THEN 1
    WHEN EXISTS (
        SELECT 1 FROM org_units c
        WHERE c.id='3efcbf8d-c992-4f57-a09d-8b8cfa4cd134'
          AND c.name='吴越三班' AND c.unit_type='CLASS'
          AND c.parent_id='org-wujiang' AND c.is_active=1
          AND (SELECT COUNT(*) FROM org_units g WHERE g.parent_id=c.id AND g.is_active=1)=5
    ) AND NOT EXISTS (SELECT 1 FROM org_units WHERE id='org-wuyue-3' AND is_active=1) THEN 1
    WHEN EXISTS (
        SELECT 1 FROM org_units c
        WHERE c.id='3efcbf8d-c992-4f57-a09d-8b8cfa4cd134'
          AND c.name='吴越三班' AND c.unit_type='CLASS'
          AND c.parent_id='org-wujiang' AND c.is_active=1
          AND (SELECT COUNT(*) FROM org_units g WHERE g.parent_id=c.id AND g.is_active=1)=5
    ) AND EXISTS (
        SELECT 1 FROM org_units s
        WHERE s.id='org-wuyue-3' AND s.name='吴越三班'
          AND s.unit_type='CLASS' AND s.parent_id='org-wujiang' AND s.is_active=1
          AND (SELECT COUNT(*) FROM org_units g WHERE g.parent_id=s.id AND g.is_active=1)=0
          AND (SELECT COUNT(*) FROM member_org_relations r JOIN members m ON m.id=r.member_id
               WHERE r.org_unit_id=s.id AND m.status='ACTIVE'
                 AND (r.valid_from IS NULL OR r.valid_from<=UTC_DATE())
                 AND (r.valid_until IS NULL OR r.valid_until>=UTC_DATE()))=29
          AND (SELECT COUNT(*) FROM attendance_event_groups e WHERE e.study_org_unit_id=s.id AND e.status='ACTIVE')=1
          AND (SELECT COUNT(*) FROM members m WHERE m.org_unit_id=s.id OR m.development_org_unit_id=s.id)=0
          AND (SELECT COUNT(*) FROM member_org_relations source JOIN members m ON m.id=source.member_id
               JOIN member_org_relations target ON target.member_id=source.member_id
                    AND target.relation_type=source.relation_type
                    AND target.org_unit_id='3efcbf8d-c992-4f57-a09d-8b8cfa4cd134'
               WHERE source.org_unit_id=s.id AND m.status='ACTIVE'
                 AND (source.valid_from IS NULL OR source.valid_from<=UTC_DATE())
                 AND (source.valid_until IS NULL OR source.valid_until>=UTC_DATE()))=19
          AND (SELECT COUNT(*) FROM member_org_relations source JOIN members m ON m.id=source.member_id
               WHERE source.org_unit_id=s.id AND m.status='ACTIVE'
                 AND (source.valid_from IS NULL OR source.valid_from<=UTC_DATE())
                 AND (source.valid_until IS NULL OR source.valid_until>=UTC_DATE())
                 AND NOT EXISTS (SELECT 1 FROM member_org_relations target
                     WHERE target.member_id=source.member_id AND target.relation_type=source.relation_type
                       AND target.org_unit_id='3efcbf8d-c992-4f57-a09d-8b8cfa4cd134'))=10
    ) THEN 1 ELSE 0
END;

INSERT INTO audit_logs (actor_user_id,action,resource_type,resource_id,org_unit_id,purpose,result,before_json,after_json,created_at)
SELECT (SELECT id FROM app_users WHERE username='admin' ORDER BY id LIMIT 1),
    'org.class_name_duplicate.merge','org_unit','org-wuyue-3','3efcbf8d-c992-4f57-a09d-8b8cfa4cd134',
    '业务负责人确认合并重复吴越三班：29名在册中19名已在正式班级，迁移其余10名与1个活动，停用重复节点','SUCCESS',
    JSON_OBJECT('source_id','org-wuyue-3','active_member_relations',29,'already_canonical',19,'move_active_relations',10,'inactive_history_relations',1,'active_events',1),
    JSON_OBJECT('target_id','3efcbf8d-c992-4f57-a09d-8b8cfa4cd134','source_deactivated',TRUE,'rollback_requires_review',TRUE),UTC_TIMESTAMP()
WHERE EXISTS (SELECT 1 FROM org_units WHERE id='3efcbf8d-c992-4f57-a09d-8b8cfa4cd134')
  AND EXISTS (SELECT 1 FROM org_units WHERE id='org-wuyue-3' AND is_active=1);

UPDATE attendance_event_groups SET study_org_unit_id='3efcbf8d-c992-4f57-a09d-8b8cfa4cd134',updated_at=UTC_TIMESTAMP()
WHERE study_org_unit_id='org-wuyue-3';

UPDATE member_org_relations source
JOIN members m ON m.id=source.member_id
JOIN member_org_relations target ON target.member_id=source.member_id
 AND target.relation_type=source.relation_type AND target.org_unit_id='3efcbf8d-c992-4f57-a09d-8b8cfa4cd134'
SET source.valid_until=DATE_SUB(UTC_DATE(), INTERVAL 1 DAY),source.updated_at=UTC_TIMESTAMP()
WHERE source.org_unit_id='org-wuyue-3' AND m.status='ACTIVE'
  AND (source.valid_from IS NULL OR source.valid_from<=UTC_DATE())
  AND (source.valid_until IS NULL OR source.valid_until>=UTC_DATE());

UPDATE member_org_relations source
JOIN members m ON m.id=source.member_id
LEFT JOIN member_org_relations target ON target.member_id=source.member_id
 AND target.relation_type=source.relation_type AND target.org_unit_id='3efcbf8d-c992-4f57-a09d-8b8cfa4cd134'
SET source.org_unit_id='3efcbf8d-c992-4f57-a09d-8b8cfa4cd134',source.updated_at=UTC_TIMESTAMP()
WHERE source.org_unit_id='org-wuyue-3' AND m.status='ACTIVE'
  AND (source.valid_from IS NULL OR source.valid_from<=UTC_DATE())
  AND (source.valid_until IS NULL OR source.valid_until>=UTC_DATE()) AND target.id IS NULL;

UPDATE org_units SET is_active=0,active_until=UTC_DATE(),updated_at=UTC_TIMESTAMP() WHERE id='org-wuyue-3';

DROP TABLE migration_guard_0024_merge_duplicate_wuyue_three;
