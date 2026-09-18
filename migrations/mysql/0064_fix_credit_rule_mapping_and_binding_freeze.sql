-- 0064: G5.4 C0 rule-policy mapping and frozen binding references.
--
-- This is deliberately fail-closed.  The 0048 INSERT IGNORE could leave the
-- audited course-policy identity as DRAFT when the identity already existed.
-- A partial or edited course directory is not repaired by this migration:
-- it must be reconciled by an explicit policy operation first.

START TRANSACTION;

CREATE TEMPORARY TABLE g5_4_c0_expected_course_rules (
    course_key VARCHAR(128) PRIMARY KEY,
    course_name VARCHAR(255) NOT NULL,
    year_index INT NULL,
    credit_points INT NOT NULL
);
INSERT INTO g5_4_c0_expected_course_rules(course_key, course_name, year_index, credit_points) VALUES
    ('Y1-HAPPINESS-ASSESSMENT', '幸福测评表讲解', 1, 20),
    ('Y1-CLASS-SPEECH-DRAFT', '班级学习会发表稿编写讲解', 1, 20),
    ('Y1-SIX-DILIGENCES', '六项精进实践', 1, 20),
    ('Y1-TWELVE-MANAGEMENT', '经营十二条实践', 1, 20),
    ('Y1-INTEGRATED-ACCOUNTING', '整体核算表编制', 1, 40),
    ('Y1-ACCOUNTING-ANALYSIS-TASK', '核算表分析与任务单制作', 1, 40),
    ('Y1-SEVEN-ACCOUNTING-PRINCIPLES', '会计七原则实践', 1, 20),
    ('Y1-KYOCERA-ANNUAL-PLAN', '京瓷如何制定年度计划', 1, 40),
    ('Y1-ANNUAL-MONTHLY-MGMT', '年度计划与月度核算管理', 1, 11),
    ('GM-LEARNING-PRACTICE-COMMITTEE', '学习践行委培训', NULL, 34),
    ('GM-HAPPINESS-CARE-COMMITTEE', '幸福关爱委培训', NULL, 34),
    ('GM-IMPROVEMENT-INNOVATION-TRAINING', '改善创新委培训 + 如何改善创新', NULL, 34),
    ('GM-OPERATING-ANALYSIS-BASIC', '如何召开经营分析会（基础版）', NULL, 40),
    ('GM-OPERATING-ANALYSIS-ADVANCED', '如何召开经营分析会（进阶版）', NULL, 30),
    ('AUTO-QR-AMOEBA-INTRODUCTION', '阿米巴经营之概论', 2, 20),
    ('GM-AMOEBA-DEPARTMENT-ACCOUNTING', '阿米巴经营之如何制作分部门核算表', NULL, 20),
    ('GM-INNOVATION-CASE-SHARING', '创新案例分享（塾内优秀企业家）', NULL, 20),
    ('GM-PHILOSOPHY-MANUAL-1', '哲学手册编制 1', NULL, 30),
    ('GM-PHILOSOPHY-MANUAL-2', '哲学手册编制 2', NULL, 30),
    ('GM-HUMAN-FINANCE-SYSTEM-1', '人财培养体系 1', NULL, 35),
    ('GM-HUMAN-FINANCE-SYSTEM-2', '人财培养体系 2', NULL, 35),
    ('GM-HUMAN-FINANCE-SYSTEM-3', '人财培养体系 3', NULL, 35),
    ('AUTO-QR-HUNDRED-DAY-CAMPAIGN', '如何开展百日奋战', 3, 20),
    ('GM-VOLUNTEER-TRAINING-1', '志愿者学长培训视频1（预备班工作开展、正式班开班典礼、日常读书）', NULL, 25),
    ('GM-VOLUNTEER-TRAINING-2', '志愿者学长培训视频2（班级学习会、小组学习会、学员走访、学分、幸福测评）', NULL, 24);

-- A clean installation has the 0048-created target version but no persisted
-- course rows.  Seed only that exact empty state from the frozen definition;
-- a partial/non-empty state is never overwritten or completed implicitly.
INSERT INTO learning_plan_credit_rules
    (rule_version_id, course_key, course_name, year_index, credit_points,
     status, source, aliases_json, created_at, updated_at)
SELECT v.id, e.course_key, e.course_name, e.year_index, e.credit_points,
       'CONFIGURED', 'BASELINE', '[]', UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM learning_plan_credit_rule_versions v
CROSS JOIN g5_4_c0_expected_course_rules e
WHERE v.plan_key='STANDARD_3Y_2026'
  AND v.version_label='2026.1'
  AND NOT EXISTS (
      SELECT 1 FROM learning_plan_credit_rules existing
      WHERE existing.rule_version_id=v.id
  );

CREATE TEMPORARY TABLE g5_4_c0_expected_generic_rules (
    rule_key VARCHAR(128) PRIMARY KEY,
    settlement_model VARCHAR(32) NOT NULL,
    points DECIMAL(10,2) NULL,
    cap_points DECIMAL(10,2) NULL
);
INSERT INTO g5_4_c0_expected_generic_rules(rule_key, settlement_model, points, cap_points) VALUES
    ('DAILY_READING', 'DAILY_ONCE', 1, NULL),
    ('EXCELLENT_SHARE', 'MONTHLY_CAP', 1, 5),
    ('GROUP_MEETING_ATTENDANCE', 'CYCLE_ONCE', 4, NULL),
    ('COURSE_COMPLETION', 'COURSE_COMPLETION', NULL, NULL),
    ('CLASS_MEETING_SCORE', 'EVENT_ONCE', NULL, NULL),
    ('MANUAL_ADJUSTMENT', 'MANUAL_ADJUSTMENT', NULL, NULL);

-- Every non-zero result below violates a precondition and aborts the
-- migration through the CHECK constraint.  No mapping or binding is changed
-- before all guards have passed.
CREATE TEMPORARY TABLE g5_4_c0_guard (n INT CHECK(n=0));
INSERT INTO g5_4_c0_guard
SELECT CASE WHEN COUNT(*)=1 AND SUM(CASE WHEN status IN ('DRAFT','PUBLISHED') THEN 1 ELSE 0 END)=1
                 AND SUM(CASE WHEN based_on_version_label='2026' THEN 1 ELSE 0 END)=1
            THEN 0 ELSE 1 END
FROM learning_plan_credit_rule_versions
WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1';
INSERT INTO g5_4_c0_guard
SELECT CASE WHEN COUNT(*)=25 THEN 0 ELSE 1 END
FROM learning_plan_credit_rules r
JOIN learning_plan_credit_rule_versions v ON v.id=r.rule_version_id
WHERE v.plan_key='STANDARD_3Y_2026' AND v.version_label='2026.1';
INSERT INTO g5_4_c0_guard
SELECT COUNT(*)
FROM g5_4_c0_expected_course_rules e
LEFT JOIN learning_plan_credit_rules r
  ON r.course_key=e.course_key
LEFT JOIN learning_plan_credit_rule_versions v
  ON v.id=r.rule_version_id
 AND v.plan_key='STANDARD_3Y_2026' AND v.version_label='2026.1'
WHERE v.id IS NULL
   OR r.id IS NULL
   OR r.course_name<>e.course_name
   OR (r.year_index<>e.year_index OR (r.year_index IS NULL)<>(e.year_index IS NULL))
   OR r.credit_points<>e.credit_points
   OR r.status<>'CONFIGURED';
INSERT INTO g5_4_c0_guard
SELECT COUNT(*)
FROM learning_plan_credit_rules r
JOIN learning_plan_credit_rule_versions v ON v.id=r.rule_version_id
LEFT JOIN g5_4_c0_expected_course_rules e ON e.course_key=r.course_key
WHERE v.plan_key='STANDARD_3Y_2026' AND v.version_label='2026.1'
  AND e.course_key IS NULL;
INSERT INTO g5_4_c0_guard
SELECT CASE WHEN COUNT(*)=1 AND SUM(CASE WHEN status='PUBLISHED' THEN 1 ELSE 0 END)=1
            THEN 0 ELSE 1 END
FROM learning_credit_rule_versions
WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1';
INSERT INTO g5_4_c0_guard
SELECT CASE WHEN COUNT(*)=6 THEN 0 ELSE 1 END
FROM learning_credit_rules r
JOIN learning_credit_rule_versions v ON v.id=r.rule_version_id
WHERE v.rule_set_key='STANDARD_3Y_2026' AND v.version_label='2026.1'
;
INSERT INTO g5_4_c0_guard
SELECT COUNT(*)
FROM g5_4_c0_expected_generic_rules e
LEFT JOIN learning_credit_rules r ON r.rule_key=e.rule_key
LEFT JOIN learning_credit_rule_versions v
  ON v.id=r.rule_version_id
 AND v.rule_set_key='STANDARD_3Y_2026' AND v.version_label='2026.1'
WHERE v.id IS NULL
   OR r.id IS NULL
   OR r.settlement_model<>e.settlement_model
   OR (r.points<>e.points OR (r.points IS NULL)<>(e.points IS NULL))
   OR (r.cap_points<>e.cap_points OR (r.cap_points IS NULL)<>(e.cap_points IS NULL))
   OR r.credit_category<>'STANDARD_LEARNING'
   OR r.status<>'ACTIVE';
INSERT INTO g5_4_c0_guard
SELECT COUNT(*)
FROM learning_credit_rules r
JOIN learning_credit_rule_versions v ON v.id=r.rule_version_id
LEFT JOIN g5_4_c0_expected_generic_rules e ON e.rule_key=r.rule_key
WHERE v.rule_set_key='STANDARD_3Y_2026' AND v.version_label='2026.1'
  AND e.rule_key IS NULL;
INSERT INTO g5_4_c0_guard
SELECT COUNT(*)
FROM learning_plan_credit_rule_mappings m
WHERE m.plan_key='standard-3y' AND m.plan_version_label='2026'
  AND NOT (
      m.status='ACTIVE'
      AND m.generic_rule_version_id=(SELECT id FROM learning_credit_rule_versions WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1')
      AND m.course_credit_rule_version_id=(SELECT id FROM learning_plan_credit_rule_versions WHERE plan_key='STANDARD_3Y_2026' AND version_label='2026.1')
  );
DROP TEMPORARY TABLE g5_4_c0_guard;

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

DROP TEMPORARY TABLE g5_4_c0_expected_generic_rules;
DROP TEMPORARY TABLE g5_4_c0_expected_course_rules;

COMMIT;
