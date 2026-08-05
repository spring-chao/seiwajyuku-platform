-- 0014: add encrypted renewal-preview storage columns.
-- Historical-row redaction is intentionally a separate, approved migration;
-- this schema migration must not destroy existing preview evidence.
ALTER TABLE renewal_import_batches ADD COLUMN preview_ciphertext LONGTEXT NULL;

ALTER TABLE renewal_import_staging ADD COLUMN history_note_ciphertext LONGTEXT NULL;
ALTER TABLE renewal_import_staging ADD COLUMN assistance_note_ciphertext LONGTEXT NULL;
ALTER TABLE renewal_import_staging ADD COLUMN raw_json_ciphertext LONGTEXT NULL;
