"""Bounded dev/test cleanup. Metadata/audit retained; dry run unless --apply."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/platform-api"))
from app.services.study_meeting_evidence import cleanup_evidence

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    report = cleanup_evidence(apply=args.apply, limit=args.limit)
    print(json.dumps(report))
    raise SystemExit(1 if report["errors"] else 0)
