-- Refuse a destructive rollback after codes have been issued. Use backup/forward repair.
CREATE TEMPORARY TABLE attendance_entry_0068_guard (n INT CHECK(n=0));
INSERT INTO attendance_entry_0068_guard SELECT COUNT(*) FROM attendance_entry_tokens;
DROP TEMPORARY TABLE attendance_entry_0068_guard;
DROP TABLE attendance_entry_tokens;
DELETE FROM schema_migrations WHERE version='0068_signin_management.sql';
