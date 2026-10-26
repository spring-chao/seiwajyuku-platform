-- Empty-schema reversal only. Preserve every captured month and attribution.
CREATE TEMPORARY TABLE study_roster_rollback_guard(ok INTEGER NOT NULL CHECK(ok=1));
INSERT INTO study_roster_rollback_guard(ok)
SELECT CASE WHEN (SELECT COUNT(*) FROM study_meeting_roster_months)=0
 AND (SELECT COUNT(*) FROM study_meeting_roster_members)=0
 AND (SELECT COUNT(*) FROM study_meeting_roster_groups)=0 THEN 1 ELSE 0 END;
DROP TABLE study_meeting_roster_members;
DROP TABLE study_meeting_roster_groups;
DROP TABLE study_meeting_roster_months;
DELETE FROM schema_migrations WHERE version='0073_study_meeting_monthly_roster.sql';
DROP TABLE study_roster_rollback_guard;
