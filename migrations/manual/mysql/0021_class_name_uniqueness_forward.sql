-- Run only after POST /api/v1/iam/org-units/class-name-cleanup reports zero
-- duplicates. MySQL uses a generated column to emulate a partial unique key.
ALTER TABLE org_units
    ADD COLUMN active_class_name VARCHAR(255)
    GENERATED ALWAYS AS (
        CASE
            WHEN is_active=1 AND unit_type IN ('CLASS', 'SPECIAL_COHORT')
            THEN name
            ELSE NULL
        END
    ) STORED,
    ADD UNIQUE KEY uq_active_class_name (active_class_name);
