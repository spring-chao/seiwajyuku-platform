-- Repair existing installations where migration 0009 was already marked applied
-- before the default three-session score rules were added.
-- Older 0009 installations may have the table without its unique index. Add it
-- before using the conflict target so the repair remains safe and idempotent.
CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_score_rule_version
    ON attendance_score_rules(activity_type, session_code, rule_version);

INSERT INTO attendance_score_rules
    (rule_version, activity_type, session_code, base_points, late_deduction,
     early_leave_deduction, effective_from, status, created_at)
VALUES
    (1, 'CLASS_MEETING', 'MORNING', 7, 1, 1, '2026-01-01', 'ACTIVE', datetime('now')),
    (1, 'CLASS_MEETING', 'AFTERNOON', 7, 1, 1, '2026-01-01', 'ACTIVE', datetime('now')),
    (1, 'CLASS_MEETING', 'KONPA', 4, 1, 1, '2026-01-01', 'ACTIVE', datetime('now'))
ON CONFLICT(activity_type, session_code, rule_version) DO UPDATE SET
    status='ACTIVE',
    effective_from=MIN(effective_from, excluded.effective_from);
