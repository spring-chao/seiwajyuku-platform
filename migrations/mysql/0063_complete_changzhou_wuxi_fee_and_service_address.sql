-- 0063: complete the confirmed annual member service-fee facts.
-- 0062 remains immutable; this additive migration only fills the previously
-- unconfirmed Changzhou/Wuxi fee and service-address fields.

START TRANSACTION;
CREATE TEMPORARY TABLE shuku_fee_0063_forward_guard (n INT CHECK(n=0));
INSERT INTO shuku_fee_0063_forward_guard
SELECT COUNT(*)
FROM enrollment_shuku_profile_terms
WHERE (shuku_org_unit_id='org-changzhou' AND NOT (
          (fee_amount IS NULL AND fee_unit IS NULL AND service_address IS NULL)
          OR (
              fee_amount=4800
              AND fee_unit='元/人/年'
              AND service_address='常州市天宁区青洋北路与西歧路交叉口西南140米福北工业园'
          )
      ))
   OR (shuku_org_unit_id='org-wuxi' AND NOT (
          (fee_amount IS NULL AND fee_unit IS NULL AND service_address IS NULL)
          OR (
              fee_amount=4800
              AND fee_unit='元/人/年'
              AND service_address='无锡市滨湖区雪浪街道蠡湖大道2008号（蠡湖大道与震泽路交叉口）中邦蠡湖商务园35号'
          )
      ));
DROP TEMPORARY TABLE shuku_fee_0063_forward_guard;

UPDATE enrollment_shuku_profile_terms
SET fee_amount=4800,
    fee_unit='元/人/年',
    service_address='常州市天宁区青洋北路与西歧路交叉口西南140米福北工业园',
    is_active=1,
    updated_at=UTC_TIMESTAMP()
WHERE shuku_org_unit_id='org-changzhou'
  AND EXISTS (
      SELECT 1 FROM enrollment_shuku_profiles
      WHERE shuku_org_unit_id='org-changzhou' AND is_active=1
  );

UPDATE enrollment_shuku_profile_terms
SET fee_amount=4800,
    fee_unit='元/人/年',
    service_address='无锡市滨湖区雪浪街道蠡湖大道2008号（蠡湖大道与震泽路交叉口）中邦蠡湖商务园35号',
    is_active=1,
    updated_at=UTC_TIMESTAMP()
WHERE shuku_org_unit_id='org-wuxi'
  AND EXISTS (
      SELECT 1 FROM enrollment_shuku_profiles
      WHERE shuku_org_unit_id='org-wuxi' AND is_active=1
  );

COMMIT;
