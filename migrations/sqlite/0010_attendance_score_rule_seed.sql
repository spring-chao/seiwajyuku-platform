-- Repair existing installations where migration 0009 was already marked applied
-- before the default three-session score rules were added.
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
