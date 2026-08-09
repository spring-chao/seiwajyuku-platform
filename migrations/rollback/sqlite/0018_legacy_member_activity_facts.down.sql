-- 0018 rollback removes imported legacy member activity facts.
-- This is destructive; run only against a verified backup or disposable clone.
DROP TABLE IF EXISTS member_activity_facts;
