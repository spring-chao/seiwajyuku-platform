-- 0018: privacy-safe member activity facts migrated from the legacy operations system.
CREATE TABLE IF NOT EXISTS member_activity_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_system TEXT NOT NULL,
    source_table TEXT NOT NULL,
    external_id TEXT NOT NULL,
    member_id INTEGER NOT NULL REFERENCES members(id),
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    activity_type TEXT NOT NULL,
    occurred_on TEXT NOT NULL,
    participation_status TEXT NOT NULL,
    title TEXT,
    duration_minutes INTEGER,
    source_updated_at TEXT,
    import_batch_id INTEGER NOT NULL REFERENCES import_batches(id),
    imported_at TEXT NOT NULL,
    UNIQUE(source_system, source_table, external_id)
);
CREATE INDEX IF NOT EXISTS idx_member_activity_fact_member
    ON member_activity_facts(member_id, occurred_on);
CREATE INDEX IF NOT EXISTS idx_member_activity_fact_org
    ON member_activity_facts(org_unit_id, occurred_on, activity_type);
