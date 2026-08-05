-- MANUAL / APPROVAL REQUIRED: redact historical renewal preview source data.
--
-- This file is intentionally outside the automatic migration directory.
-- Do not run it during application startup or a normal deployment. Execute
-- only after a verified backup and explicit approval for historical-row
-- redaction. New uploads already store encrypted copies in the *_ciphertext
-- columns added by 0014_renewal_preview_privacy.sql.
UPDATE renewal_import_batches
SET preview_json = '{"redacted":true}'
WHERE preview_json IS NOT NULL AND preview_ciphertext IS NULL;

UPDATE renewal_import_staging
SET history_note = NULL,
    assistance_note = NULL,
    raw_json = '{}'
WHERE raw_json IS NOT NULL AND raw_json <> '{}'
  AND raw_json_ciphertext IS NULL;
