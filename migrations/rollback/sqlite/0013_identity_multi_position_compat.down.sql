-- 0013 changes the SQLite position-key check constraint while preserving rows.
-- A destructive reverse rebuild is intentionally not provided; restore the
-- pre-migration database snapshot when rollback is required.
SELECT 1;
