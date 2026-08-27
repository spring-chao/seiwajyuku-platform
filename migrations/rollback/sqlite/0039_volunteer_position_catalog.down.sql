-- M2 catalog is additive. Keep the rebuilt volunteer_appointments table intact
-- so that historical appointments are never destroyed or silently invalidated.
DROP TABLE IF EXISTS volunteer_position_capabilities;
DROP TABLE IF EXISTS volunteer_position_catalog;
