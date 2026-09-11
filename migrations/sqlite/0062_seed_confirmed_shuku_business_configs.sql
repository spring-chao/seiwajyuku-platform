-- 0062: seed the business-confirmed public enrollment profiles.
-- 0061 remains the empty profile capability; this migration records the
-- confirmed facts for Suzhou, Changzhou and Wuxi only.

CREATE TABLE IF NOT EXISTS enrollment_shuku_profile_terms (
    shuku_org_unit_id TEXT PRIMARY KEY
        REFERENCES enrollment_shuku_profiles(shuku_org_unit_id),
    fee_amount NUMERIC(12, 2),
    fee_unit TEXT,
    service_address TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS enrollment_shuku_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shuku_org_unit_id TEXT NOT NULL
        REFERENCES enrollment_shuku_profiles(shuku_org_unit_id),
    contact_name TEXT NOT NULL,
    contact_phone TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(shuku_org_unit_id, contact_phone)
);

CREATE INDEX IF NOT EXISTS idx_enrollment_shuku_contacts_lookup
    ON enrollment_shuku_contacts(shuku_org_unit_id, is_active, sort_order, id);

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
    1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE EXISTS (
    SELECT 1 FROM org_units WHERE id='org-suzhou' AND unit_code='SZ_ROOT' AND is_active=1
)
UNION ALL SELECT
    'org-changzhou', '常州塾',
    '提交资料后，工作人员将审核并与您联系；具体入塾条件及会费以工作人员确认的信息为准。',
    NULL, '无锡稻合企业管理顾问有限公司', '中信银行常州天宁支行', '8110501011602342242',
    '常夏', '15380081186', NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE EXISTS (
    SELECT 1 FROM org_units WHERE id='org-changzhou' AND unit_code='CZ_ROOT' AND is_active=1
)
UNION ALL SELECT
    'org-wuxi', '无锡塾',
    '提交资料后，工作人员将审核并与您联系；具体入塾条件及会费以工作人员确认的信息为准。',
    NULL, '无锡稻合企业管理顾问有限公司', '中国工商银行股份有限公司无锡人民东路支行', '1103054509100146820',
    '春晴', '18051598063', NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE EXISTS (
    SELECT 1 FROM org_units WHERE id='org-wuxi' AND unit_code='WX_ROOT' AND is_active=1
)
ON CONFLICT(shuku_org_unit_id) DO UPDATE SET
    display_name=excluded.display_name,
    joining_notice=excluded.joining_notice,
    payment_instructions=excluded.payment_instructions,
    payee_name=excluded.payee_name,
    bank_name=excluded.bank_name,
    bank_account=excluded.bank_account,
    contact_name=excluded.contact_name,
    contact_phone=excluded.contact_phone,
    contact_address=excluded.contact_address,
    is_active=excluded.is_active,
    updated_at=excluded.updated_at;

INSERT INTO enrollment_shuku_profile_terms
    (shuku_org_unit_id, fee_amount, fee_unit, service_address,
     is_active, created_at, updated_at)
SELECT 'org-suzhou', 4800, '元/人/年', '苏州市高新区竹园路189号2幢102室2楼',
       1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-suzhou')
UNION ALL
SELECT 'org-changzhou', NULL, NULL, NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-changzhou')
UNION ALL
SELECT 'org-wuxi', NULL, NULL, NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-wuxi')
ON CONFLICT(shuku_org_unit_id) DO UPDATE SET
    fee_amount=excluded.fee_amount,
    fee_unit=excluded.fee_unit,
    service_address=excluded.service_address,
    is_active=excluded.is_active,
    updated_at=excluded.updated_at;

INSERT INTO enrollment_shuku_contacts
    (shuku_org_unit_id, contact_name, contact_phone, sort_order,
     is_active, created_at, updated_at)
SELECT 'org-suzhou', '张玲嫒', '19984864833', 10, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-suzhou')
UNION ALL
SELECT 'org-suzhou', '胡延辉', '13776052728', 20, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-suzhou')
UNION ALL
SELECT 'org-changzhou', '常夏', '15380081186', 10, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-changzhou')
UNION ALL
SELECT 'org-changzhou', '常德', '15366836286', 20, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-changzhou')
UNION ALL
SELECT 'org-wuxi', '春晴', '18051598063', 10, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-wuxi')
UNION ALL
SELECT 'org-wuxi', '尹琦', '18021183718', 20, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE EXISTS (SELECT 1 FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-wuxi')
ON CONFLICT(shuku_org_unit_id, contact_phone) DO UPDATE SET
    contact_name=excluded.contact_name,
    sort_order=excluded.sort_order,
    is_active=excluded.is_active,
    updated_at=excluded.updated_at;
