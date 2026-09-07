-- C7.1.2.3 吴越三班权威历史快照：只读、参数化导出查询。
--
-- This file is for an authorized data owner / DBA only.  Bind these parameters
-- using the SQL client's native parameter facility; do not interpolate strings
-- into a shared copy of this file:
--   :class_name   = 吴越三班
--   :window_start = 2026-08-31
--   :window_end   = 2026-09-04
--   :class_org_unit_id = filled only after query 01 returns exactly one row.
--
-- Start one consistent read-only transaction before query 01 and end it with
-- ROLLBACK after all result sets have been privately exported as UTF-8 CSV.
-- Do not use SELECT *, INTO OUTFILE, or any write/DDL statement.

-- 01. Export as class_candidates.csv.  Stop unless exactly one row is returned.
SELECT
    id,
    name,
    unit_type,
    parent_id,
    active_from,
    active_until,
    is_active
FROM org_units
WHERE name = :class_name
  AND unit_type = 'CLASS'
  AND (active_from IS NULL OR active_from <= :window_end)
  AND (active_until IS NULL OR active_until >= :window_start)
ORDER BY id;

-- 02. Export as org_units.csv.  It includes only the chosen class, its direct
-- parent reference, and all direct GROUP nodes, including currently retired
-- nodes that were historically effective inside the requested window.
SELECT
    ou.id,
    ou.name,
    ou.unit_type,
    ou.parent_id,
    ou.active_from,
    ou.active_until,
    ou.is_active
FROM org_units AS ou
WHERE ou.id = :class_org_unit_id
   OR ou.id = (
       SELECT parent_id FROM org_units WHERE id = :class_org_unit_id
   )
   OR (ou.parent_id = :class_org_unit_id AND ou.unit_type = 'GROUP')
ORDER BY ou.unit_type, ou.id;

-- 03a. Export as members.csv when masked account data is not required.
SELECT DISTINCT
    m.id,
    m.name,
    m.member_code,
    m.status
FROM members AS m
JOIN member_org_relations AS r ON r.member_id = m.id
LEFT JOIN org_units AS g ON g.id = r.org_unit_id
WHERE r.relation_type IN ('STUDY_CLASS', 'STUDY_GROUP')
  AND (
      r.org_unit_id = :class_org_unit_id
      OR (g.parent_id = :class_org_unit_id AND g.unit_type = 'GROUP')
  )
  AND (r.valid_from IS NULL OR r.valid_from <= :window_end)
  AND (r.valid_until IS NULL OR r.valid_until >= :window_start)
ORDER BY m.id;

-- 03b. Run instead of 03a only when the HQ exported masked account is needed
-- to disambiguate candidates.  The result still must not contain full phones.
-- SELECT DISTINCT
--     m.id,
--     m.name,
--     m.member_code,
--     m.status,
--     m.phone_masked,
--     m.phone_last4
-- FROM members AS m
-- JOIN member_org_relations AS r ON r.member_id = m.id
-- LEFT JOIN org_units AS g ON g.id = r.org_unit_id
-- WHERE r.relation_type IN ('STUDY_CLASS', 'STUDY_GROUP')
--   AND (
--       r.org_unit_id = :class_org_unit_id
--       OR (g.parent_id = :class_org_unit_id AND g.unit_type = 'GROUP')
--   )
--   AND (r.valid_from IS NULL OR r.valid_from <= :window_end)
--   AND (r.valid_until IS NULL OR r.valid_until >= :window_start)
-- ORDER BY m.id;

-- 04. Export as member_org_relations.csv.  Preserve original relation windows;
-- do not reduce them to today's membership.
SELECT
    r.id,
    r.member_id,
    r.org_unit_id,
    r.relation_type,
    r.is_primary,
    r.valid_from,
    r.valid_until,
    r.source_type
FROM member_org_relations AS r
LEFT JOIN org_units AS g ON g.id = r.org_unit_id
WHERE r.relation_type IN ('STUDY_CLASS', 'STUDY_GROUP')
  AND (
      r.org_unit_id = :class_org_unit_id
      OR (g.parent_id = :class_org_unit_id AND g.unit_type = 'GROUP')
  )
  AND (r.valid_from IS NULL OR r.valid_from <= :window_end)
  AND (r.valid_until IS NULL OR r.valid_until >= :window_start)
ORDER BY r.member_id, r.relation_type, r.org_unit_id, r.id;

-- 05. Export as class_learning_bindings.csv.  Do not filter by current ACTIVE
-- status: a historically frozen round may now be ENDED or use RETIRED rules.
SELECT
    id,
    class_org_unit_id,
    plan_version_id,
    cohort_month,
    started_at,
    ended_at,
    status,
    learning_round,
    transition_type,
    credit_rule_version_id,
    course_credit_rule_version_id
FROM class_learning_bindings
WHERE class_org_unit_id = :class_org_unit_id
ORDER BY started_at, id;

-- 06. Export as class_learning_cycles.csv.
SELECT
    c.id,
    c.binding_id,
    c.class_org_unit_id,
    c.learning_cycle_index,
    c.plan_cycle_id,
    c.opened_at,
    c.actual_class_meeting_at,
    c.cycle_status,
    c.closed_at
FROM class_learning_cycles AS c
JOIN class_learning_bindings AS b ON b.id = c.binding_id
WHERE b.class_org_unit_id = :class_org_unit_id
ORDER BY c.binding_id, c.learning_cycle_index, c.id;

-- 07. Export as learning_plan_versions.csv.
SELECT DISTINCT
    p.id,
    p.plan_key,
    p.plan_name,
    p.version_label,
    p.duration_cycles,
    p.status
FROM learning_plan_versions AS p
JOIN class_learning_bindings AS b ON b.plan_version_id = p.id
WHERE b.class_org_unit_id = :class_org_unit_id
ORDER BY p.id;

-- 08. Export as schema_migrations.csv.  These rows identify the migration
-- floor needed by the isolated replay; no other schema-migration history is
-- necessary for this C7 gate.
SELECT
    version,
    applied_at
FROM schema_migrations
WHERE version >= '0045' AND version <= '0050'
ORDER BY version;

-- Optional dependency exports: run only if the fresh isolated database lacks
-- the frozen generic rule records or the 2026 CHINA_MAINLAND business calendar.
-- Export names and fields must match field-whitelist.md exactly.

-- learning_credit_rule_versions.csv
-- SELECT v.id, v.rule_set_key, v.version_label, v.status
-- FROM learning_credit_rule_versions AS v
-- JOIN class_learning_bindings AS b ON b.credit_rule_version_id = v.id
-- WHERE b.class_org_unit_id = :class_org_unit_id
-- ORDER BY v.id;

-- learning_credit_rules.csv
-- SELECT r.id, r.rule_version_id, r.rule_key, r.credit_category,
--        r.credit_type, r.settlement_model, r.status
-- FROM learning_credit_rules AS r
-- JOIN class_learning_bindings AS b ON b.credit_rule_version_id = r.rule_version_id
-- WHERE b.class_org_unit_id = :class_org_unit_id
-- ORDER BY r.rule_version_id, r.id;

-- learning_business_calendar_versions.csv
-- SELECT id, calendar_key, calendar_year, version_label, timezone, status
-- FROM learning_business_calendar_versions
-- WHERE calendar_key = 'CHINA_MAINLAND'
--   AND calendar_year = YEAR(:window_start)
-- ORDER BY id;

-- learning_business_calendar_days.csv
-- SELECT d.calendar_version_id, d.business_date, d.day_type, d.note
-- FROM learning_business_calendar_days AS d
-- JOIN learning_business_calendar_versions AS v ON v.id = d.calendar_version_id
-- WHERE v.calendar_key = 'CHINA_MAINLAND'
--   AND v.calendar_year = YEAR(:window_start)
--   AND d.business_date BETWEEN :window_start AND :window_end
-- ORDER BY d.business_date, d.id;
