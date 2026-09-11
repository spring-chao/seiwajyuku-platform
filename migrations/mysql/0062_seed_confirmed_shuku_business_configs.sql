-- 0062: seed the business-confirmed public enrollment profiles.
-- 0061 remains the empty profile capability; this migration records the
-- confirmed facts for Suzhou, Changzhou and Wuxi only.

CREATE TABLE IF NOT EXISTS enrollment_shuku_profile_terms (
    shuku_org_unit_id VARCHAR(64) PRIMARY KEY,
    fee_amount DECIMAL(12, 2) NULL,
    fee_unit VARCHAR(64) NULL,
    service_address VARCHAR(1000) NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_enrollment_shuku_terms_profile
        FOREIGN KEY(shuku_org_unit_id)
        REFERENCES enrollment_shuku_profiles(shuku_org_unit_id),
    INDEX idx_enrollment_shuku_terms_active(is_active, shuku_org_unit_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS enrollment_shuku_contacts (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    shuku_org_unit_id VARCHAR(64) NOT NULL,
    contact_name VARCHAR(255) NOT NULL,
    contact_phone VARCHAR(64) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_enrollment_shuku_contacts_profile
        FOREIGN KEY(shuku_org_unit_id)
        REFERENCES enrollment_shuku_profiles(shuku_org_unit_id),
    UNIQUE KEY uq_enrollment_shuku_contact_phone(shuku_org_unit_id, contact_phone),
    INDEX idx_enrollment_shuku_contacts_lookup(shuku_org_unit_id, is_active, sort_order, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO enrollment_shuku_profiles
    (shuku_org_unit_id, display_name, joining_notice, payment_instructions,
     payee_name, bank_name, bank_account, contact_name, contact_phone,
     contact_address, is_active, created_at, updated_at)
SELECT
    'org-suzhou', '苏州塾',
    '学长服务助手面向认同稻盛哲学、经营学理念的企业经营者，提供相互学习、相互交流和共同成长的学习服务，致力于让幸福企业遍布江南。每一位准备加入本学习服务的学员，均需认真阅读并遵守加入守则。',
    '转账后请将转账截图提交工作人员。收到会费后，工作人员将开具电子发票，并通过微信或邮箱提供。',
    '无锡稻合企业管理顾问有限公司', '招商银行苏州新区支行', '512914112210201',
    '张玲嫒', '19984864833', '苏州市高新区竹园路189号2幢102室2楼',
    1, UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM (SELECT 1) AS seed
WHERE EXISTS (
    SELECT 1 FROM org_units WHERE id='org-suzhou' AND unit_code='SZ_ROOT' AND is_active=1
)
UNION ALL SELECT
    'org-changzhou', '常州塾',
    '提交资料后，工作人员将审核并与您联系；具体入塾条件及会费以工作人员确认的信息为准。',
    NULL, '无锡稻合企业管理顾问有限公司', '中信银行常州天宁支行', '8110501011602342242',
    '常夏', '15380081186', NULL, 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM (SELECT 1) AS seed
WHERE EXISTS (
    SELECT 1 FROM org_units WHERE id='org-changzhou' AND unit_code='CZ_ROOT' AND is_active=1
)
UNION ALL SELECT
    'org-wuxi', '无锡塾',
    '提交资料后，工作人员将审核并与您联系；具体入塾条件及会费以工作人员确认的信息为准。',
    NULL, '无锡稻合企业管理顾问有限公司', '中国工商银行股份有限公司无锡人民东路支行', '1103054509100146820',
    '春晴', '18051598063', NULL, 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM (SELECT 1) AS seed
WHERE EXISTS (
    SELECT 1 FROM org_units WHERE id='org-wuxi' AND unit_code='WX_ROOT' AND is_active=1
)
ON DUPLICATE KEY UPDATE
    display_name=VALUES(display_name),
    joining_notice=VALUES(joining_notice),
    payment_instructions=VALUES(payment_instructions),
    payee_name=VALUES(payee_name),
    bank_name=VALUES(bank_name),
    bank_account=VALUES(bank_account),
    contact_name=VALUES(contact_name),
    contact_phone=VALUES(contact_phone),
    contact_address=VALUES(contact_address),
    is_active=VALUES(is_active),
    updated_at=VALUES(updated_at);

INSERT INTO enrollment_shuku_profile_terms
    (shuku_org_unit_id, fee_amount, fee_unit, service_address,
     is_active, created_at, updated_at)
SELECT 'org-suzhou', 4800, '元/人/年', '苏州市高新区竹园路189号2幢102室2楼',
       1, UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM (SELECT 1) AS seed
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-suzhou')
UNION ALL
SELECT 'org-changzhou', NULL, NULL, NULL, 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM (SELECT 1) AS seed
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-changzhou')
UNION ALL
SELECT 'org-wuxi', NULL, NULL, NULL, 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM (SELECT 1) AS seed
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-wuxi')
ON DUPLICATE KEY UPDATE
    fee_amount=VALUES(fee_amount),
    fee_unit=VALUES(fee_unit),
    service_address=VALUES(service_address),
    is_active=VALUES(is_active),
    updated_at=VALUES(updated_at);

INSERT INTO enrollment_shuku_contacts
    (shuku_org_unit_id, contact_name, contact_phone, sort_order,
     is_active, created_at, updated_at)
SELECT 'org-suzhou', '张玲嫒', '19984864833', 10, 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM (SELECT 1) AS seed
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-suzhou')
UNION ALL
SELECT 'org-suzhou', '胡延辉', '13776052728', 20, 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM (SELECT 1) AS seed
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-suzhou')
UNION ALL
SELECT 'org-changzhou', '常夏', '15380081186', 10, 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM (SELECT 1) AS seed
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-changzhou')
UNION ALL
SELECT 'org-changzhou', '常德', '15366836286', 20, 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM (SELECT 1) AS seed
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-changzhou')
UNION ALL
SELECT 'org-wuxi', '春晴', '18051598063', 10, 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM (SELECT 1) AS seed
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-wuxi')
UNION ALL
SELECT 'org-wuxi', '尹琦', '18021183718', 20, 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()
FROM (SELECT 1) AS seed
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-wuxi')
ON DUPLICATE KEY UPDATE
    contact_name=VALUES(contact_name),
    sort_order=VALUES(sort_order),
    is_active=VALUES(is_active),
    updated_at=VALUES(updated_at);
