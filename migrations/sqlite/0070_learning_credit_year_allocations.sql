CREATE TABLE learning_credit_year_imports (
 id INTEGER PRIMARY KEY AUTOINCREMENT, content_fingerprint TEXT NOT NULL UNIQUE,
 file_sha256 TEXT NOT NULL, original_filename TEXT NOT NULL,
 binding_id INTEGER NOT NULL REFERENCES class_learning_bindings(id),
 year_index INTEGER NOT NULL CHECK(year_index BETWEEN 1 AND 3), cutoff_date TEXT NOT NULL,
 source_note TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL' CHECK(status IN ('PENDING_APPROVAL','APPROVED','POSTED','CANCELLED')),
 created_by INTEGER NOT NULL REFERENCES app_users(id), approved_by INTEGER REFERENCES app_users(id),
 posted_by INTEGER REFERENCES app_users(id), created_at TEXT NOT NULL, approved_at TEXT, posted_at TEXT
);
CREATE TABLE learning_credit_year_rows (
 id INTEGER PRIMARY KEY AUTOINCREMENT, import_id INTEGER NOT NULL REFERENCES learning_credit_year_imports(id),
 member_id INTEGER NOT NULL REFERENCES members(id), opening_import_id INTEGER NOT NULL REFERENCES learning_credit_opening_imports(id),
 excel_row INTEGER NOT NULL, points NUMERIC NOT NULL CHECK(points>=0), UNIQUE(import_id,member_id)
);
CREATE TABLE learning_credit_year_allocations (
 member_id INTEGER NOT NULL REFERENCES members(id), opening_import_id INTEGER NOT NULL REFERENCES learning_credit_opening_imports(id),
 binding_id INTEGER NOT NULL REFERENCES class_learning_bindings(id), year_index INTEGER NOT NULL CHECK(year_index BETWEEN 1 AND 3),
 points NUMERIC NOT NULL CHECK(points>=0), import_id INTEGER NOT NULL REFERENCES learning_credit_year_imports(id),
 PRIMARY KEY(member_id,opening_import_id,year_index)
);
CREATE TABLE learning_credit_class_progress (
 binding_id INTEGER PRIMARY KEY REFERENCES class_learning_bindings(id), completed_days INTEGER NOT NULL CHECK(completed_days IN (12,24,36)),
 cutoff_date TEXT NOT NULL, import_id INTEGER NOT NULL REFERENCES learning_credit_year_imports(id)
);
