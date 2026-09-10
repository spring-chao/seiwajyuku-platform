-- 0060: persist the shuku a prospective member is applying to.
-- NULL is intentionally retained for historical applications and links that
-- predate this field. The service treats those rows as legacy review data.

ALTER TABLE member_enrollment_links
    ADD COLUMN target_shuku_org_unit_id TEXT REFERENCES org_units(id);

ALTER TABLE member_enrollment_applications
    ADD COLUMN target_shuku_org_unit_id TEXT REFERENCES org_units(id);

CREATE INDEX IF NOT EXISTS idx_enrollment_link_target_shuku
    ON member_enrollment_links(target_shuku_org_unit_id);
CREATE INDEX IF NOT EXISTS idx_enrollment_app_target_shuku
    ON member_enrollment_applications(target_shuku_org_unit_id, application_status, created_at);
