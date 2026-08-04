-- 0013: allow the compatibility 苏州塾运营管理员 position key.
-- SQLite cannot alter a CHECK constraint in place, so rebuild this additive
-- table while preserving all existing position assignments.
PRAGMA foreign_keys=OFF;

CREATE TABLE operations_position_assignments_compat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employment_id INTEGER NOT NULL REFERENCES operations_employments(id) ON DELETE CASCADE,
    position_key TEXT NOT NULL CHECK (position_key IN (
        'operations_admin',
        'ops_center_director',
        'ops_center_operations',
        'ops_center_learning',
        'ops_center_development',
        'ops_center_management',
        'ops_center_data',
        'ops_center_finance',
        'ops_center_administration'
    )),
    valid_from TEXT,
    valid_until TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('PLANNED', 'ACTIVE', 'SUSPENDED', 'ENDED', 'REVOKED')),
    source_reference TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO operations_position_assignments_compat
    (id, employment_id, position_key, valid_from, valid_until, status,
     source_reference, created_at, updated_at)
SELECT id, employment_id, position_key, valid_from, valid_until, status,
       source_reference, created_at, updated_at
FROM operations_position_assignments;

DROP TABLE operations_position_assignments;
ALTER TABLE operations_position_assignments_compat
    RENAME TO operations_position_assignments;
CREATE INDEX idx_position_assignments_employment
    ON operations_position_assignments(employment_id, status, valid_from, valid_until);

PRAGMA foreign_keys=ON;
