-- Destructive rollback for 0009. Back up or export required attendance data first.
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS attendance_sync_runs;
DROP TABLE IF EXISTS attendance_adjudications;
DROP TABLE IF EXISTS attendance_score_records;
DROP TABLE IF EXISTS attendance_score_rules;
DROP TABLE IF EXISTS attendance_records;
DROP TABLE IF EXISTS attendance_sessions;
DROP TABLE IF EXISTS attendance_event_groups;
SET FOREIGN_KEY_CHECKS = 1;
