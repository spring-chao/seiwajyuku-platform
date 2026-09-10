-- 0058: land the confirmed Wuxi organization tree.
-- Jingjin is a SPECIAL_COHORT directly under Wuxi; it must not be placed
-- under either guidance operating unit.

INSERT IGNORE INTO org_units
    (id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at)
VALUES
    ('org-wuxi-guidance-1', 'WX_GUIDANCE_1', '指导一团', 'OPERATING_UNIT', 'org-wuxi', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-guidance-2', 'WX_GUIDANCE_2', '指导二团', 'OPERATING_UNIT', 'org-wuxi', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-ling-hang-1', 'WX_LING_HANG_1', '领航1班', 'CLASS', 'org-wuxi-guidance-1', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-ling-hang-2', 'WX_LING_HANG_2', '领航2班', 'CLASS', 'org-wuxi-guidance-1', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-ling-hang-3', 'WX_LING_HANG_3', '领航3班', 'CLASS', 'org-wuxi-guidance-1', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-zhi-cheng-1', 'WX_ZHI_CHENG_1', '志成1班', 'CLASS', 'org-wuxi-guidance-1', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-zhi-cheng-2', 'WX_ZHI_CHENG_2', '志成2班', 'CLASS', 'org-wuxi-guidance-1', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-xiong-ying-1', 'WX_XIONG_YING_1', '雄鹰1班', 'CLASS', 'org-wuxi-guidance-1', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-xiong-ying-2', 'WX_XIONG_YING_2', '雄鹰2班', 'CLASS', 'org-wuxi-guidance-1', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-xiong-ying-3', 'WX_XIONG_YING_3', '雄鹰3班', 'CLASS', 'org-wuxi-guidance-1', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-ju-long-1', 'WX_JU_LONG_1', '聚龙1班', 'CLASS', 'org-wuxi-guidance-2', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-ju-long-2', 'WX_JU_LONG_2', '聚龙2班', 'CLASS', 'org-wuxi-guidance-2', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-xing-huo-1', 'WX_XING_HUO_1', '星火1班', 'CLASS', 'org-wuxi-guidance-2', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-xing-huo-2', 'WX_XING_HUO_2', '星火2班', 'CLASS', 'org-wuxi-guidance-2', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-xing-huo-3', 'WX_XING_HUO_3', '星火3班', 'CLASS', 'org-wuxi-guidance-2', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-zhuo-yue-1', 'WX_ZHUO_YUE_1', '卓越1班', 'CLASS', 'org-wuxi-guidance-2', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-zhuo-yue-2', 'WX_ZHUO_YUE_2', '卓越2班', 'CLASS', 'org-wuxi-guidance-2', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-zhuo-yue-3', 'WX_ZHUO_YUE_3', '卓越3班', 'CLASS', 'org-wuxi-guidance-2', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP()),
    ('org-wuxi-jing-jin', 'WX_JING_JIN', '精进班', 'SPECIAL_COHORT', 'org-wuxi', 1, UTC_TIMESTAMP(), UTC_TIMESTAMP());
