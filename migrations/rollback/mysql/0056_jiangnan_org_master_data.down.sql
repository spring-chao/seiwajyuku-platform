-- 0056 rollback is loss-averse. It is only safe before the new institution or
-- org roots receive staff, member, volunteer, attendance, or learning data.
START TRANSACTION;

CREATE TEMPORARY TABLE jiangnan_org_master_rollback_guard (n INT CHECK(n=0));
INSERT INTO jiangnan_org_master_rollback_guard
SELECT
    (SELECT COUNT(*) FROM org_units WHERE parent_id='org-jiangnan'
        AND id NOT IN ('org-suzhou','org-changzhou','org-wuxi'))
  + (SELECT COUNT(*) FROM org_units WHERE parent_id='org-changzhou'
        AND id NOT IN ('org-changzhou-tian-ning','org-changzhou-wu-jin',
                       'org-changzhou-zhong-lou','org-changzhou-jing-kai',
                       'org-changzhou-xin-bei','org-changzhou-jian-xing',
                       'org-changzhou-long-cheng'))
  + (SELECT COUNT(*) FROM org_units WHERE parent_id IN (
        'org-changzhou-tian-ning','org-changzhou-wu-jin','org-changzhou-zhong-lou',
        'org-changzhou-jing-kai','org-changzhou-xin-bei','org-changzhou-jian-xing',
        'org-changzhou-long-cheng','org-wuxi'))
  + (SELECT COUNT(*) FROM operations_employments
        WHERE institution_id IN ('institution-changzhou','institution-wuxi'))
  + (SELECT COUNT(*) FROM data_scope_grants WHERE org_unit_id IN (
        'org-jiangnan','org-suzhou','org-changzhou','org-wuxi',
        'org-changzhou-tian-ning','org-changzhou-wu-jin','org-changzhou-zhong-lou',
        'org-changzhou-jing-kai','org-changzhou-xin-bei','org-changzhou-jian-xing',
        'org-changzhou-long-cheng'))
  + (SELECT COUNT(*) FROM member_org_relations WHERE org_unit_id IN (
        'org-jiangnan','org-suzhou','org-changzhou','org-wuxi',
        'org-changzhou-tian-ning','org-changzhou-wu-jin','org-changzhou-zhong-lou',
        'org-changzhou-jing-kai','org-changzhou-xin-bei','org-changzhou-jian-xing',
        'org-changzhou-long-cheng'))
  + (SELECT COUNT(*) FROM employee_service_responsibilities WHERE org_unit_id IN (
        'org-jiangnan','org-suzhou','org-changzhou','org-wuxi',
        'org-changzhou-tian-ning','org-changzhou-wu-jin','org-changzhou-zhong-lou',
        'org-changzhou-jing-kai','org-changzhou-xin-bei','org-changzhou-jian-xing',
        'org-changzhou-long-cheng'))
  + (SELECT COUNT(*) FROM volunteer_appointments WHERE org_unit_id IN (
        'org-jiangnan','org-suzhou','org-changzhou','org-wuxi',
        'org-changzhou-tian-ning','org-changzhou-wu-jin','org-changzhou-zhong-lou',
        'org-changzhou-jing-kai','org-changzhou-xin-bei','org-changzhou-jian-xing',
        'org-changzhou-long-cheng'))
  + (SELECT COUNT(*) FROM members WHERE org_unit_id IN (
        'org-jiangnan','org-suzhou','org-changzhou','org-wuxi',
        'org-changzhou-tian-ning','org-changzhou-wu-jin','org-changzhou-zhong-lou',
        'org-changzhou-jing-kai','org-changzhou-xin-bei','org-changzhou-jian-xing',
        'org-changzhou-long-cheng') OR development_org_unit_id IN (
        'org-jiangnan','org-suzhou','org-changzhou','org-wuxi',
        'org-changzhou-tian-ning','org-changzhou-wu-jin','org-changzhou-zhong-lou',
        'org-changzhou-jing-kai','org-changzhou-xin-bei','org-changzhou-jian-xing',
        'org-changzhou-long-cheng'))
  + (SELECT COUNT(*) FROM followup_tasks WHERE org_unit_id IN (
        'org-jiangnan','org-suzhou','org-changzhou','org-wuxi',
        'org-changzhou-tian-ning','org-changzhou-wu-jin','org-changzhou-zhong-lou',
        'org-changzhou-jing-kai','org-changzhou-xin-bei','org-changzhou-jian-xing',
        'org-changzhou-long-cheng'))
  + (SELECT COUNT(*) FROM attendance_event_groups WHERE org_unit_id IN (
        'org-jiangnan','org-suzhou','org-changzhou','org-wuxi',
        'org-changzhou-tian-ning','org-changzhou-wu-jin','org-changzhou-zhong-lou',
        'org-changzhou-jing-kai','org-changzhou-xin-bei','org-changzhou-jian-xing',
        'org-changzhou-long-cheng') OR study_org_unit_id IN (
        'org-jiangnan','org-suzhou','org-changzhou','org-wuxi',
        'org-changzhou-tian-ning','org-changzhou-wu-jin','org-changzhou-zhong-lou',
        'org-changzhou-jing-kai','org-changzhou-xin-bei','org-changzhou-jian-xing',
        'org-changzhou-long-cheng'))
  + (SELECT COUNT(*) FROM volunteer_service_units WHERE home_shuku_org_unit_id IN (
        'org-jiangnan','org-suzhou','org-changzhou','org-wuxi',
        'org-changzhou-tian-ning','org-changzhou-wu-jin','org-changzhou-zhong-lou',
        'org-changzhou-jing-kai','org-changzhou-xin-bei','org-changzhou-jian-xing',
        'org-changzhou-long-cheng') OR service_target_org_unit_id IN (
        'org-jiangnan','org-suzhou','org-changzhou','org-wuxi',
        'org-changzhou-tian-ning','org-changzhou-wu-jin','org-changzhou-zhong-lou',
        'org-changzhou-jing-kai','org-changzhou-xin-bei','org-changzhou-jian-xing',
        'org-changzhou-long-cheng'));
DROP TEMPORARY TABLE jiangnan_org_master_rollback_guard;

DELETE FROM institution_org_links
WHERE institution_id IN ('institution-changzhou', 'institution-wuxi')
   OR (institution_id IN ('institution-seiwa-hq', 'institution-jiangnan', 'institution-suzhou')
       AND org_unit_id IN ('org-jiangnan', 'org-suzhou', 'org-changzhou', 'org-wuxi')
       AND link_type='SERVICE_BOUNDARY');

DELETE FROM operating_institutions
WHERE id IN ('institution-changzhou', 'institution-wuxi');

UPDATE org_units
SET parent_id=NULL, updated_at=UTC_TIMESTAMP()
WHERE id='org-suzhou' AND parent_id='org-jiangnan';

DELETE FROM org_units
WHERE id IN (
    'org-changzhou-tian-ning', 'org-changzhou-wu-jin',
    'org-changzhou-zhong-lou', 'org-changzhou-jing-kai',
    'org-changzhou-xin-bei', 'org-changzhou-jian-xing',
    'org-changzhou-long-cheng', 'org-changzhou', 'org-wuxi', 'org-jiangnan'
);

DELETE FROM schema_migrations WHERE version='0056_jiangnan_org_master_data.sql';
COMMIT;
