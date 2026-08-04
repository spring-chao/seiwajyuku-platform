-- 0014: keep renewal preview source data encrypted at rest.
-- Existing previews are intentionally redacted and must be re-uploaded if
-- the original workbook is still needed for a new review batch.
ALTER TABLE renewal_import_batches ADD COLUMN preview_ciphertext TEXT;
UPDATE renewal_import_batches
SET preview_json = '{"redacted":true}'
WHERE preview_json IS NOT NULL;

ALTER TABLE renewal_import_staging ADD COLUMN history_note_ciphertext TEXT;
ALTER TABLE renewal_import_staging ADD COLUMN assistance_note_ciphertext TEXT;
ALTER TABLE renewal_import_staging ADD COLUMN raw_json_ciphertext TEXT;
UPDATE renewal_import_staging
SET history_note = NULL,
    assistance_note = NULL,
    raw_json = '{}';
