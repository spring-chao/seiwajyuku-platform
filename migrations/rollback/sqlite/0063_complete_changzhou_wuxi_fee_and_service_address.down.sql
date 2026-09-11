-- 0063 rollback is conservative: only revert rows that still contain the
-- exact values introduced by this migration.  Business edits abort rollback.
BEGIN;
CREATE TEMP TABLE shuku_fee_0063_rollback_guard (n INTEGER CHECK(n=0));
INSERT INTO shuku_fee_0063_rollback_guard
SELECT COUNT(*)
FROM enrollment_shuku_profile_terms
WHERE (shuku_org_unit_id='org-changzhou' AND NOT (
          fee_amount=4800
          AND fee_unit='元/人/年'
          AND service_address='常州市天宁区青洋北路与西歧路交叉口西南140米福北工业园'
      ))
   OR (shuku_org_unit_id='org-wuxi' AND NOT (
          fee_amount=4800
          AND fee_unit='元/人/年'
          AND service_address='无锡市滨湖区雪浪街道蠡湖大道2008号（蠡湖大道与震泽路交叉口）中邦蠡湖商务园35号'
      ));
DROP TABLE shuku_fee_0063_rollback_guard;

UPDATE enrollment_shuku_profile_terms
SET fee_amount=NULL,
    fee_unit=NULL,
    service_address=NULL,
    updated_at=CURRENT_TIMESTAMP
WHERE shuku_org_unit_id IN ('org-changzhou', 'org-wuxi');

DELETE FROM schema_migrations
WHERE version='0063_complete_changzhou_wuxi_fee_and_service_address.sql';
COMMIT;
