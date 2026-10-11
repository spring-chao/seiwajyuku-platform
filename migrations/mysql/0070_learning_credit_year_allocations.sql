-- Classify confirmed opening balances; never add or reprice points.
CREATE TABLE learning_credit_year_imports (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 content_fingerprint CHAR(64) NOT NULL UNIQUE,
 file_sha256 CHAR(64) NOT NULL,
 original_filename VARCHAR(255) NOT NULL,
 binding_id BIGINT NOT NULL,
 year_index INT NOT NULL CHECK(year_index BETWEEN 1 AND 3),
 cutoff_date DATE NOT NULL,
 source_note VARCHAR(1000) NOT NULL,
 status VARCHAR(32) NOT NULL DEFAULT 'PENDING_APPROVAL' CHECK(status IN ('PENDING_APPROVAL','APPROVED','POSTED','CANCELLED')),
 created_by BIGINT NOT NULL, approved_by BIGINT NULL, posted_by BIGINT NULL,
 created_at DATETIME NOT NULL, approved_at DATETIME NULL, posted_at DATETIME NULL,
 FOREIGN KEY(binding_id) REFERENCES class_learning_bindings(id),
 FOREIGN KEY(created_by) REFERENCES app_users(id),
 FOREIGN KEY(approved_by) REFERENCES app_users(id),
 FOREIGN KEY(posted_by) REFERENCES app_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE learning_credit_year_rows (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 import_id BIGINT NOT NULL, member_id BIGINT NOT NULL, opening_import_id BIGINT NOT NULL,
 excel_row INT NOT NULL, points DECIMAL(10,2) NOT NULL CHECK(points>=0),
 UNIQUE(import_id,member_id),
 FOREIGN KEY(import_id) REFERENCES learning_credit_year_imports(id),
 FOREIGN KEY(member_id) REFERENCES members(id),
 FOREIGN KEY(opening_import_id) REFERENCES learning_credit_opening_imports(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE learning_credit_year_allocations (
 member_id BIGINT NOT NULL, opening_import_id BIGINT NOT NULL, binding_id BIGINT NOT NULL,
 year_index INT NOT NULL CHECK(year_index BETWEEN 1 AND 3),
 points DECIMAL(10,2) NOT NULL CHECK(points>=0), import_id BIGINT NOT NULL,
 PRIMARY KEY(member_id,opening_import_id,year_index),
 FOREIGN KEY(member_id) REFERENCES members(id),
 FOREIGN KEY(opening_import_id) REFERENCES learning_credit_opening_imports(id),
 FOREIGN KEY(binding_id) REFERENCES class_learning_bindings(id),
 FOREIGN KEY(import_id) REFERENCES learning_credit_year_imports(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE learning_credit_class_progress (
 binding_id BIGINT PRIMARY KEY,
 completed_days INT NOT NULL CHECK(completed_days IN (12,24,36)),
 cutoff_date DATE NOT NULL, import_id BIGINT NOT NULL,
 FOREIGN KEY(binding_id) REFERENCES class_learning_bindings(id),
 FOREIGN KEY(import_id) REFERENCES learning_credit_year_imports(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
