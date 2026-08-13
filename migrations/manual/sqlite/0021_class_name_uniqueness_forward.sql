-- Run only after POST /api/v1/iam/org-units/class-name-cleanup reports zero
-- duplicates. This keeps inactive historical nodes and other org types valid.
CREATE UNIQUE INDEX uq_active_class_name
    ON org_units(name)
    WHERE is_active=1 AND unit_type IN ('CLASS', 'SPECIAL_COHORT');
