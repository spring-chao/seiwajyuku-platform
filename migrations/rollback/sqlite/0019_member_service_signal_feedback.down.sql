-- 0019 rollback removes member service signal feedback history.
-- Run only against a verified backup or disposable clone.
DROP TABLE IF EXISTS member_service_signal_feedback;
