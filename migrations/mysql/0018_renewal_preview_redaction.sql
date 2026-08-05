-- 0018: redact historical renewal preview source data after explicit approval.
-- New uploads already store encrypted copies in the *_ciphertext columns.
UPDATE renewal_import_batches
SET preview_json = JSON_OBJECT('redacted', TRUE)
WHERE preview_json IS NOT NULL AND preview_ciphertext IS NULL;

UPDATE renewal_import_staging
SET history_note = NULL,
    assistance_note = NULL,
    raw_json = JSON_OBJECT()
WHERE raw_json IS NOT NULL AND raw_json <> JSON_OBJECT()
  AND raw_json_ciphertext IS NULL;
