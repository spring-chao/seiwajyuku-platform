-- Lookup support for the application-enforced class-name uniqueness rule.
-- Existing production duplicates are reconciled through the audited API before
-- the manual database constraint is applied.
CREATE INDEX IF NOT EXISTS idx_active_class_name
    ON org_units(name)
    WHERE is_active=1 AND unit_type IN ('CLASS', 'SPECIAL_COHORT');
