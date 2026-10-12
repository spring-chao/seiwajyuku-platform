CREATE TABLE learning_credit_source_imports (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 fingerprint TEXT NOT NULL UNIQUE, file_sha256 TEXT NOT NULL,
 original_filename TEXT NOT NULL, source_kind TEXT NOT NULL CHECK(source_kind IN ('CLASS_MEETING','DAILY_READING','EXCELLENT_SHARE')),
 class_org_unit_id TEXT NOT NULL REFERENCES org_units(id),
 binding_id INTEGER NOT NULL REFERENCES class_learning_bindings(id),
 cycle_id INTEGER REFERENCES class_learning_cycles(id),
 event_group_id INTEGER REFERENCES attendance_event_groups(id),
 options_json TEXT NOT NULL, source_snapshot_json TEXT NOT NULL, source_note TEXT NOT NULL,
 created_by INTEGER NOT NULL REFERENCES app_users(id), created_at TEXT NOT NULL
);
CREATE TABLE learning_credit_source_rows (
 id INTEGER PRIMARY KEY AUTOINCREMENT, import_id INTEGER NOT NULL REFERENCES learning_credit_source_imports(id),
 member_id INTEGER NOT NULL REFERENCES members(id), excel_row INTEGER NOT NULL, occurred_on TEXT NOT NULL,
 source_reference TEXT NOT NULL, record_json TEXT NOT NULL,
 fact_id INTEGER REFERENCES learning_credit_activity_facts(id),
 attendance_record_id INTEGER REFERENCES attendance_records(id),
 UNIQUE(import_id,excel_row,source_reference)
);
CREATE TABLE learning_credit_source_batches (
 import_id INTEGER NOT NULL REFERENCES learning_credit_source_imports(id),
 batch_id INTEGER NOT NULL REFERENCES learning_credit_settlement_batches(id), PRIMARY KEY(import_id,batch_id)
);
