-- 0068: preserve historical applications; record referrals only on explicit save.
CREATE TABLE IF NOT EXISTS enrollment_application_referrer_centers (
    application_id INTEGER PRIMARY KEY REFERENCES member_enrollment_applications(id),
    referrer_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    updated_by INTEGER NOT NULL REFERENCES app_users(id),
    updated_at TEXT NOT NULL
);
