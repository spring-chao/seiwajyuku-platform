-- 0048: G5.3 学习计划版本与学分规则版本显式映射
-- 学习计划 identity（例如 standard-3y/2026）与两个学分政策 identity
-- 是独立命名空间。新绑定冻结具体版本 id；本迁移不写学分账本。

ALTER TABLE class_learning_bindings
    ADD COLUMN course_credit_rule_version_id INTEGER
        REFERENCES learning_plan_credit_rule_versions(id);
CREATE INDEX IF NOT EXISTS idx_class_learning_bindings_course_credit_rule
    ON class_learning_bindings(course_credit_rule_version_id);

CREATE TABLE IF NOT EXISTS learning_plan_credit_rule_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_key TEXT NOT NULL,
    plan_version_label TEXT NOT NULL,
    generic_rule_version_id INTEGER NOT NULL
        REFERENCES learning_credit_rule_versions(id),
    course_credit_rule_version_id INTEGER NOT NULL
        REFERENCES learning_plan_credit_rule_versions(id),
    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK(status IN ('ACTIVE', 'RETIRED')),
    mapping_source TEXT NOT NULL DEFAULT 'G5.3',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(plan_key, plan_version_label)
);
CREATE INDEX IF NOT EXISTS idx_learning_plan_credit_rule_mappings_status
    ON learning_plan_credit_rule_mappings(plan_key, plan_version_label, status);

-- The audited 2026 course-policy identity is independent from the learning
-- plan identity.  Do not promote an already-existing DRAFT silently; an
-- explicit publish operation remains required in that case.
INSERT OR IGNORE INTO learning_plan_credit_rule_versions
    (plan_key, version_label, status, based_on_version_label, created_at, updated_at)
VALUES
    ('STANDARD_3Y_2026', '2026.1', 'PUBLISHED', '2026', datetime('now'), datetime('now'));

-- This is the only current production mapping.  The policy ids are resolved
-- by their own identities and then frozen onto each class learning binding.
INSERT OR IGNORE INTO learning_plan_credit_rule_mappings
    (plan_key, plan_version_label, generic_rule_version_id,
     course_credit_rule_version_id, status, mapping_source, created_at, updated_at)
SELECT 'standard-3y', '2026', generic.id, course.id, 'ACTIVE', 'G5.3',
       datetime('now'), datetime('now')
FROM learning_credit_rule_versions generic
JOIN learning_plan_credit_rule_versions course
  ON course.plan_key='STANDARD_3Y_2026' AND course.version_label='2026.1'
WHERE generic.rule_set_key='STANDARD_3Y_2026'
  AND generic.version_label='2026.1'
  AND generic.status='PUBLISHED'
  AND course.status='PUBLISHED';

-- Safe backfill: only the explicitly mapped learning-plan identity is
-- corrected.  No course points or historical facts are rewritten.
UPDATE class_learning_bindings
SET credit_rule_version_id=COALESCE(
        credit_rule_version_id,
        (
            SELECT m.generic_rule_version_id
            FROM learning_plan_credit_rule_mappings m
            JOIN learning_plan_versions p
              ON p.plan_key=m.plan_key AND p.version_label=m.plan_version_label
            WHERE p.id=class_learning_bindings.plan_version_id
              AND m.plan_key='standard-3y'
              AND m.plan_version_label='2026'
              AND m.status='ACTIVE'
            LIMIT 1
        )
    ),
    course_credit_rule_version_id=COALESCE(
        course_credit_rule_version_id,
        (
            SELECT m.course_credit_rule_version_id
            FROM learning_plan_credit_rule_mappings m
            JOIN learning_plan_versions p
              ON p.plan_key=m.plan_key AND p.version_label=m.plan_version_label
            WHERE p.id=class_learning_bindings.plan_version_id
              AND m.plan_key='standard-3y'
              AND m.plan_version_label='2026'
              AND m.status='ACTIVE'
            LIMIT 1
        )
    )
WHERE EXISTS (
    SELECT 1
    FROM learning_plan_versions p
    JOIN learning_plan_credit_rule_mappings m
      ON m.plan_key=p.plan_key AND m.plan_version_label=p.version_label
     AND m.plan_key='standard-3y' AND m.plan_version_label='2026'
     AND m.status='ACTIVE'
    WHERE p.id=class_learning_bindings.plan_version_id
);

-- Existing course facts retain their frozen points; only a missing/legacy
-- policy reference is completed when the owning binding is the explicit 2026
-- mapping above.
UPDATE study_meeting_courses
SET credit_rule_version_id=(
    SELECT b.course_credit_rule_version_id
    FROM study_meeting_sessions s
    JOIN class_learning_cycles lc ON lc.id=s.learning_cycle_id
    JOIN class_learning_bindings b ON b.id=lc.binding_id
    JOIN learning_plan_versions p ON p.id=b.plan_version_id
    WHERE s.id=study_meeting_courses.study_meeting_session_id
      AND p.plan_key='standard-3y' AND p.version_label='2026'
)
WHERE EXISTS (
    SELECT 1
    FROM study_meeting_sessions s
    JOIN class_learning_cycles lc ON lc.id=s.learning_cycle_id
    JOIN class_learning_bindings b ON b.id=lc.binding_id
    JOIN learning_plan_versions p ON p.id=b.plan_version_id
    WHERE s.id=study_meeting_courses.study_meeting_session_id
      AND p.plan_key='standard-3y' AND p.version_label='2026'
      AND b.course_credit_rule_version_id IS NOT NULL
)
  AND study_meeting_courses.credit_rule_version_id IS NULL;
