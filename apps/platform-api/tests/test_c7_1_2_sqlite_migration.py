from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def test_c7_1_2_sqlite_forward_and_empty_rollback_in_disposable_database() -> None:
    api_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="c7-1-2-sqlite-") as temp_dir:
        db_path = Path(temp_dir) / "migration.db"
        environment = os.environ.copy()
        environment.update(
            {
                "APP_ENV": "test",
                "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
                "DEPLOYMENT_READ_ONLY": "false",
                "RUN_BOOTSTRAP_ON_STARTUP": "false",
                "ALLOW_PRODUCTION_MUTATIONS": "false",
                "LEARNING_CREDIT_SETTLEMENT_ENABLED": "false",
                "BOOTSTRAP_ADMIN_PASSWORD": "test-admin-password",
                "PYTHONPATH": str(api_root),
            }
        )
        script = r'''
import os
import sqlite3
from app.migrations import MIGRATION_ROOT, run_migrations

applied = run_migrations()
assert "0050_c7_hq_reading_import.sql" in applied
rollback = MIGRATION_ROOT / "rollback" / "sqlite" / "0050_c7_hq_reading_import.down.sql"
connection = sqlite3.connect(os.environ["DATABASE_URL"].removeprefix("sqlite:///"))
try:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(rollback.read_text(encoding="utf-8"))
    assert connection.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type='table' AND name LIKE 'hq_reading_%'"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM schema_migrations "
        "WHERE version='0050_c7_hq_reading_import.sql'"
    ).fetchone()[0] == 0
finally:
    connection.close()
print("SQLite 0050 forward + empty rollback OK")
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=api_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "SQLite 0050 forward + empty rollback OK" in result.stdout
