-- 0014: keep renewal preview source data encrypted at rest.
-- Existing previews are intentionally redacted; they must be re-uploaded if
-- the original workbook is still needed for a new review batch.
ALTER TABLE renewal_import_batches ADD COLUMN preview_ciphertext LONGTEXT NULL;
UPDATE renewal_import_batches
SET preview_json = JSON_OBJECT('redacted', TRUE)
WHERE preview_json IS NOT NULL;

ALTER TABLE renewal_import_staging ADD COLUMN history_note_ciphertext LONGTEXT NULL;
ALTER TABLE renewal_import_staging ADD COLUMN assistance_note_ciphertext LONGTEXT NULL;
ALTER TABLE renewal_import_staging ADD COLUMN raw_json_ciphertext LONGTEXT NULL;
UPDATE renewal_import_staging
SET history_note = NULL,
    assistance_note = NULL,
    raw_json = JSON_OBJECT();
