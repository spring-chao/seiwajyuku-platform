from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))
os.environ.setdefault("APP_ENV", "test")
test_database = Path(tempfile.gettempdir()) / (
    f"seiwajyuku_platform_test_{os.getpid()}.db"
)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{test_database.as_posix()}")
os.environ.setdefault("ALLOW_PRODUCTION_MUTATIONS", "false")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "test-admin-password")
