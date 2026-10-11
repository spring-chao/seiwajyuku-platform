CREATE TABLE learning_credit_year_reconciliation_rows (
 import_id INTEGER NOT NULL REFERENCES learning_credit_year_imports(id),
 member_id INTEGER NOT NULL REFERENCES members(id),
 source_name TEXT NOT NULL, member_code TEXT NOT NULL,
 previous_import_id INTEGER REFERENCES learning_credit_opening_imports(id),
 previous_points NUMERIC NOT NULL CHECK(previous_points>=0),
 supplement_points NUMERIC NOT NULL CHECK(supplement_points>=0),
 PRIMARY KEY(import_id,member_id)
);
CREATE TABLE learning_credit_opening_adjustments (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 member_id INTEGER NOT NULL REFERENCES members(id),
 import_id INTEGER NOT NULL REFERENCES learning_credit_opening_imports(id),
 year_import_id INTEGER NOT NULL REFERENCES learning_credit_year_imports(id),
 org_unit_id TEXT NOT NULL REFERENCES org_units(id),
 points NUMERIC NOT NULL CHECK(points>0), cutoff_date TEXT NOT NULL,
 posted_by INTEGER NOT NULL REFERENCES app_users(id), posted_at TEXT NOT NULL,
 UNIQUE(member_id,year_import_id)
);
