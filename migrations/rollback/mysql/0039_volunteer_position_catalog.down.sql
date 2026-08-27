-- M2 catalog is additive. Keep volunteer_appointments intact so that historical
-- appointments (including any future configured key) are never destroyed.
DROP TABLE IF EXISTS volunteer_position_capabilities;
DROP TABLE IF EXISTS volunteer_position_catalog;
