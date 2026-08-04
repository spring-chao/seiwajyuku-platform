from __future__ import annotations

import json
from datetime import datetime

from app.core.privacy import decrypt_text, phone_hash
from app.db import execute, fetch_one, transaction
from app.services.renewals import (
    _linked_member_id,
    add_followup,
    list_followups,
    save_preview,
    update_cycle,
)


def test_linked_member_id_prefers_unique_production_phone_match() -> None:
    phone = "13800138000"
    assert _linked_member_id(
        phone,
        "主档学员",
        "org-wujiang",
        {
            phone_hash(phone): [
                {"id": 42, "name": "生产学员", "org_unit_id": "org-wujiang"}
            ]
        },
        {},
    ) == 42


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
        "SELECT preview_json, preview_ciphertext FROM renewal_import_batches WHERE id=?",
        (batch_id,),
    )
    saved = json.loads(row["preview_json"])
    assert saved["redacted"] is True
    encrypted = json.loads(decrypt_text(row["preview_ciphertext"]))
    assert encrypted["rows"][0]["raw"]["缴费日期"] == "2025-07-01 00:00:00"
    staging = fetch_one(
        "SELECT history_note, assistance_note, raw_json, raw_json_ciphertext "
        "FROM renewal_import_staging WHERE batch_id=?",
        (batch_id,),
    )
    assert staging["history_note"] is None
    assert staging["assistance_note"] is None
    assert staging["raw_json"] == "{}"
    assert json.loads(decrypt_text(staging["raw_json_ciphertext"]))["缴费日期"] == "2025-07-01 00:00:00"


def test_renewal_cycle_followup_and_status_update() -> None:
    member = fetch_one("SELECT id, org_unit_id FROM members ORDER BY id LIMIT 1")
    assert member is not None
    now = datetime.now().isoformat()
    with transaction() as connection:
        cycle_id = execute(
            connection,
            "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, created_at, updated_at) "
            "VALUES (?, 2099, ?, 7, 'PENDING_FIRST_CONTACT', ?, ?)",
            (member["id"], member["org_unit_id"], now, now),
        ).lastrowid
    followup_id = add_followup(
        cycle_id,
        1,
        channel="PHONE",
        summary="已完成首次续费联系",
        intention="继续沟通",
    )
    update_cycle(cycle_id, 1, status="IN_COMMUNICATION")
    followups = list_followups(cycle_id, 1)
    assert followups[0]["id"] == followup_id
    cycle = fetch_one("SELECT status FROM renewal_cycles WHERE id=?", (cycle_id,))
    assert cycle["status"] == "IN_COMMUNICATION"
