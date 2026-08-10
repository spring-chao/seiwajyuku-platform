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
os.environ.setdefault("IDENTITY_AUTHORIZATION_ENABLED", "true")
os.environ.setdefault("IDENTITY_ADMIN_WRITES_ENABLED", "true")
os.environ.setdefault("VOLUNTEER_SERVICE_INVITATIONS_ENABLED", "true")
os.environ.setdefault("MEMBER_SERVICE_SIGNAL_FEEDBACK_ENABLED", "true")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "test-admin-password")


def pytest_sessionstart(session) -> None:
    """Give every test selection the same isolated schema and IAM baseline."""
    from app.migrations import run_migrations
    from app.services.iam import seed_iam

    run_migrations()
    seed_iam()
