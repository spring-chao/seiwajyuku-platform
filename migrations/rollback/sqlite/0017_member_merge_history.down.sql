-- 0017 rollback removes the member merge audit history.
-- This is destructive; run only against a verified backup or disposable clone.
DROP TABLE IF EXISTS member_merge_history;
