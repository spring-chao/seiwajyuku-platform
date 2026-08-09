from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_local_identity_pilot_seed_smoke(tmp_path: Path) -> None:
    """The isolated pilot seed must exercise formal center/class/group IDs."""
    repository_root = Path(__file__).resolve().parents[3]
    database_path = tmp_path / "identity-pilot.db"
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
            "ALLOW_PRODUCTION_MUTATIONS": "false",
            "IDENTITY_AUTHORIZATION_ENABLED": "true",
            "IDENTITY_ADMIN_WRITES_ENABLED": "true",
            "VOLUNTEER_SERVICE_INVITATIONS_ENABLED": "true",
            "BOOTSTRAP_ADMIN_USERNAME": "pilot-admin",
            "BOOTSTRAP_ADMIN_PASSWORD": "abcdefghijkl",
            "PILOT_OPS_PASSWORD": "abcdefghijkl",
            "PILOT_VOLUNTEER_PASSWORD": "abcdefghijkl",
            "PILOT_COMPANION_PASSWORD": "abcdefghijkl",
            "PILOT_TECHNICAL_PASSWORD": "abcdefghijkl",
        }
    )
    result = subprocess.run(
        [sys.executable, "scripts/seed_local_identity_pilot.py"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
