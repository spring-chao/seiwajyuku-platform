-- Lookup support for the application-enforced class-name uniqueness rule.
-- The unique generated-column constraint is intentionally a manual follow-up:
-- it is applied only after the audited cleanup has removed legacy duplicates.
CREATE INDEX idx_active_class_name ON org_units(is_active, unit_type, name);
