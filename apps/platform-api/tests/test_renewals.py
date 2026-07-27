from __future__ import annotations

import json
from datetime import datetime

from app.db import fetch_one
from app.services.renewals import save_preview


def test_save_preview_serializes_excel_datetime() -> None:
    preview = {
        "source_name": "renewals.xlsx",
        "source_sha256": "a" * 64,
        "summary": {
            "total": 1,
            "matched": 1,
            "needs_review": 0,
            "invalid": 0,
            "assistance_review": 0,
        },
        "rows": [
            {
                "row_no": 2,
                "match_status": "MASTER_PHONE_EXACT",
                "member_id": None,
                "org_unit_id": "org-wujiang",
                "due_month": 7,
                "proposed_status": "IN_COMMUNICATION",
                "history_note": "",
                "assistance_note": "",
                "issue_code": None,
                "raw": {"缴费日期": datetime(2025, 7, 1)},
            }
        ],
    }

    batch_id = save_preview(preview, actor_user_id=1)

    row = fetch_one(
        "SELECT preview_json FROM renewal_import_batches WHERE id=?",
        (batch_id,),
    )
    saved = json.loads(row["preview_json"])
    assert saved["rows"][0]["raw"]["缴费日期"] == "2025-07-01 00:00:00"
