CREATE TABLE IF NOT EXISTS integration_sources (
    source_key TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    api_key_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS integration_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL REFERENCES integration_sources(source_key),
    external_id TEXT NOT NULL,
    snapshot_type TEXT NOT NULL CHECK(snapshot_type IN ('ATTENDANCE','READING')),
    org_unit_id TEXT NOT NULL REFERENCES org_units(id),
    activity_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    participant_hash TEXT,
    participant_last4 TEXT,
    eligible_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'COMPLETED',
    received_at TEXT NOT NULL,
    UNIQUE(source_key, external_id)
);
CREATE INDEX IF NOT EXISTS idx_integration_metric ON integration_snapshots(
    snapshot_type, org_unit_id, occurred_at, activity_type
);
CREATE TABLE IF NOT EXISTS integration_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL,
    snapshot_type TEXT NOT NULL,
    received_count INTEGER NOT NULL,
    inserted_count INTEGER NOT NULL,
    duplicate_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_summary TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
