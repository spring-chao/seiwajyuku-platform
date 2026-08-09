-- 0016 rollback removes the member profile audit history.
-- This is destructive; run only against a verified backup or disposable clone.
DROP TABLE IF EXISTS member_change_history;
