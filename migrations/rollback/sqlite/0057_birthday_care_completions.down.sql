-- Birthday-care completion facts are append-only. Refuse a lossy rollback
-- once an operator has recorded any completion.
BEGIN;
CREATE TEMP TABLE birthday_care_completion_rollback_guard (n INTEGER CHECK(n=0));
INSERT INTO birthday_care_completion_rollback_guard
SELECT COUNT(*) FROM birthday_care_completions;
DROP TABLE birthday_care_completion_rollback_guard;

DROP TABLE birthday_care_completions;
DELETE FROM schema_migrations WHERE version='0057_birthday_care_completions.sql';
COMMIT;
