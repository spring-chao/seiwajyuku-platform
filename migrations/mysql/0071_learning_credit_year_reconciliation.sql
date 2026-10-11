-- Append-only annual-source deficits; original balances and identities stay intact.
CREATE TABLE learning_credit_year_reconciliation_rows (
 import_id BIGINT NOT NULL, member_id BIGINT NOT NULL,
 source_name VARCHAR(255) NOT NULL, member_code VARCHAR(64) NOT NULL,
 previous_import_id BIGINT NULL, previous_points DECIMAL(10,2) NOT NULL CHECK(previous_points>=0),
 supplement_points DECIMAL(10,2) NOT NULL CHECK(supplement_points>=0),
 PRIMARY KEY(import_id,member_id),
 FOREIGN KEY(import_id) REFERENCES learning_credit_year_imports(id),
 FOREIGN KEY(member_id) REFERENCES members(id),
 FOREIGN KEY(previous_import_id) REFERENCES learning_credit_opening_imports(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE learning_credit_opening_adjustments (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 member_id BIGINT NOT NULL, import_id BIGINT NOT NULL, year_import_id BIGINT NOT NULL,
 org_unit_id VARCHAR(64) NOT NULL, points DECIMAL(10,2) NOT NULL CHECK(points>0),
 cutoff_date DATE NOT NULL, posted_by BIGINT NOT NULL, posted_at DATETIME NOT NULL,
 UNIQUE(member_id,year_import_id),
 FOREIGN KEY(member_id) REFERENCES members(id),
 FOREIGN KEY(import_id) REFERENCES learning_credit_opening_imports(id),
 FOREIGN KEY(year_import_id) REFERENCES learning_credit_year_imports(id),
 FOREIGN KEY(org_unit_id) REFERENCES org_units(id),
 FOREIGN KEY(posted_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
