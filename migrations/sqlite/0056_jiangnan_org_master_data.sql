-- 0056: land the confirmed Jiangnan multi-shuku organization master data.
-- Existing Suzhou IDs and descendants are preserved. The only change to the
-- existing Suzhou tree is attaching its existing SZ_ROOT to the new Jiangnan
-- root so a Jiangnan SUBTREE scope can cover all three shukus.

INSERT OR IGNORE INTO org_units
    (id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at)
VALUES
    ('org-jiangnan', 'JN_ROOT', '江南塾', 'ROOT', NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('org-changzhou', 'CZ_ROOT', '常州塾', 'ROOT', 'org-jiangnan', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('org-changzhou-tian-ning', 'CZ_TIAN_NING', '天宁分中心', 'REGIONAL_CENTER', 'org-changzhou', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('org-changzhou-wu-jin', 'CZ_WU_JIN', '武进分中心', 'REGIONAL_CENTER', 'org-changzhou', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('org-changzhou-zhong-lou', 'CZ_ZHONG_LOU', '钟楼分中心', 'REGIONAL_CENTER', 'org-changzhou', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('org-changzhou-jing-kai', 'CZ_JING_KAI', '经开分中心', 'REGIONAL_CENTER', 'org-changzhou', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('org-changzhou-xin-bei', 'CZ_XIN_BEI', '新北分中心', 'REGIONAL_CENTER', 'org-changzhou', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('org-changzhou-jian-xing', 'CZ_JIAN_XING', '健行分中心', 'REGIONAL_CENTER', 'org-changzhou', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('org-changzhou-long-cheng', 'CZ_LONG_CHENG', '龙城分中心', 'REGIONAL_CENTER', 'org-changzhou', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('org-wuxi', 'WX_ROOT', '无锡塾', 'ROOT', 'org-jiangnan', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

UPDATE org_units
SET parent_id='org-jiangnan', updated_at=CURRENT_TIMESTAMP
WHERE id='org-suzhou' AND unit_code='SZ_ROOT' AND is_active=1
  AND (parent_id IS NULL OR parent_id='org-jiangnan');

INSERT OR IGNORE INTO operating_institutions
    (id, institution_code, name, institution_type, parent_id, is_active, created_at, updated_at)
VALUES
    ('institution-changzhou', 'CHANGZHOU_CENTER', '常州塾', 'CITY_CENTER', 'institution-jiangnan', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('institution-wuxi', 'WUXI_CENTER', '无锡塾', 'CITY_CENTER', 'institution-jiangnan', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT OR IGNORE INTO institution_org_links(institution_id, org_unit_id, link_type, created_at)
SELECT oi.id, ou.id, 'SERVICE_BOUNDARY', CURRENT_TIMESTAMP
FROM operating_institutions oi
JOIN org_units ou ON ou.unit_code='JN_ROOT' AND ou.is_active=1
WHERE oi.institution_code='JIANGNAN' AND oi.is_active=1;

INSERT OR IGNORE INTO institution_org_links(institution_id, org_unit_id, link_type, created_at)
SELECT oi.id, ou.id, 'SERVICE_BOUNDARY', CURRENT_TIMESTAMP
FROM operating_institutions oi
JOIN org_units ou ON ou.unit_code='SZ_ROOT' AND ou.is_active=1
WHERE oi.institution_code='SUZHOU_CENTER' AND oi.is_active=1;

INSERT OR IGNORE INTO institution_org_links(institution_id, org_unit_id, link_type, created_at)
SELECT oi.id, ou.id, 'SERVICE_BOUNDARY', CURRENT_TIMESTAMP
FROM operating_institutions oi
JOIN org_units ou ON ou.unit_code='CZ_ROOT' AND ou.is_active=1
WHERE oi.institution_code='CHANGZHOU_CENTER' AND oi.is_active=1;

INSERT OR IGNORE INTO institution_org_links(institution_id, org_unit_id, link_type, created_at)
SELECT oi.id, ou.id, 'SERVICE_BOUNDARY', CURRENT_TIMESTAMP
FROM operating_institutions oi
JOIN org_units ou ON ou.unit_code='WX_ROOT' AND ou.is_active=1
WHERE oi.institution_code='WUXI_CENTER' AND oi.is_active=1;
