-- Source receipts only; no ledger entries, permissions or business gates changed.
CREATE TABLE learning_credit_source_imports (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 fingerprint CHAR(64) NOT NULL UNIQUE, file_sha256 CHAR(64) NOT NULL,
 original_filename VARCHAR(255) NOT NULL, source_kind VARCHAR(32) NOT NULL,
 class_org_unit_id VARCHAR(64) NOT NULL, binding_id BIGINT NOT NULL,
 cycle_id BIGINT NULL, event_group_id BIGINT NULL,
 options_json TEXT NOT NULL, source_snapshot_json MEDIUMTEXT NOT NULL, source_note VARCHAR(1000) NOT NULL,
 created_by BIGINT NOT NULL, created_at DATETIME NOT NULL,
 CHECK(source_kind IN ('CLASS_MEETING','DAILY_READING','EXCELLENT_SHARE')),
 FOREIGN KEY(class_org_unit_id) REFERENCES org_units(id),
 FOREIGN KEY(binding_id) REFERENCES class_learning_bindings(id),
 FOREIGN KEY(cycle_id) REFERENCES class_learning_cycles(id),
 FOREIGN KEY(event_group_id) REFERENCES attendance_event_groups(id),
 FOREIGN KEY(created_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE learning_credit_source_rows (
 id BIGINT PRIMARY KEY AUTO_INCREMENT, import_id BIGINT NOT NULL,
 member_id BIGINT NOT NULL, excel_row INT NOT NULL, occurred_on DATE NOT NULL,
 source_reference VARCHAR(128) NOT NULL, record_json TEXT NOT NULL,
 fact_id BIGINT NULL, attendance_record_id BIGINT NULL,
 UNIQUE(import_id,excel_row,source_reference),
 FOREIGN KEY(import_id) REFERENCES learning_credit_source_imports(id),
 FOREIGN KEY(member_id) REFERENCES members(id),
 FOREIGN KEY(fact_id) REFERENCES learning_credit_activity_facts(id),
 FOREIGN KEY(attendance_record_id) REFERENCES attendance_records(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE learning_credit_source_batches (
 import_id BIGINT NOT NULL, batch_id BIGINT NOT NULL,
 PRIMARY KEY(import_id,batch_id),
 FOREIGN KEY(import_id) REFERENCES learning_credit_source_imports(id),
 FOREIGN KEY(batch_id) REFERENCES learning_credit_settlement_batches(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
