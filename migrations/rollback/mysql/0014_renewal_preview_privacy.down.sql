-- 0014 rollback is destructive: encrypted preview evidence in these columns
-- will be removed. Run only against a verified backup or disposable clone.
ALTER TABLE renewal_import_batches DROP COLUMN preview_ciphertext;
ALTER TABLE renewal_import_staging DROP COLUMN history_note_ciphertext;
ALTER TABLE renewal_import_staging DROP COLUMN assistance_note_ciphertext;
ALTER TABLE renewal_import_staging DROP COLUMN raw_json_ciphertext;
