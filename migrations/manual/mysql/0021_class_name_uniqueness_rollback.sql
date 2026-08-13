ALTER TABLE org_units
    DROP INDEX uq_active_class_name,
    DROP COLUMN active_class_name;
