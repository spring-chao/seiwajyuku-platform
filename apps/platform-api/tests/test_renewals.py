from __future__ import annotations

import json
import tempfile
from asyncio import run
from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from openpyxl import Workbook
from starlette.datastructures import UploadFile

from app.api import renewals as renewals_api
from app.core.privacy import decrypt_text, phone_hash
from app.db import execute, fetch_one, transaction
from app.services.iam import create_user
from app.services.members import create_member, get_member_timeline, update_member
from app.services.renewals import (
    _linked_member_id,
    _master_index,
    add_followup,
    apply_preview,
    list_assignees,
    list_cycles,
    list_followups,
    list_overview,
    preview_result_view,
    rollback_import,
    save_preview,
    update_cycle,
)


@pytest.mark.parametrize(
    "headers",
    [
        ["姓名", "手机号码", "所在分中心"],
        ["name", "phone", "center"],
    ],
)
def test_master_index_accepts_chinese_and_standardized_headers(headers: list[str]) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "master.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "2026 新在册表"
        sheet.append(headers)
        sheet.append(["测试学长", "13800138000", "吴江分中心"])
        workbook.save(path)
        workbook.close()

        by_phone, by_name_center = _master_index(path)

    assert by_phone["13800138000"][0]["source_row"] == 2
    assert by_name_center[("测试学长", "吴江分中心")][0]["source_row"] == 2


def test_read_only_import_preview_does_not_persist(monkeypatch) -> None:
    preview = {
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
                "name": "测试学长",
                "center_name": "吴江分中心",
                "class_name": "吴越一班",
                "due_month": 7,
                "match_status": "NEEDS_REVIEW",
                "issue_code": "MEMBER_NOT_MATCHED",
                "proposed_status": "PENDING_FIRST_CONTACT",
                "history_note": "",
                "assistance_note": "",
                "member_id": None,
                "raw": {"手机号": "13800138000"},
            }
        ],
    }
    monkeypatch.setattr(renewals_api, "preview_workbook", lambda *_: preview)
    monkeypatch.setattr(
        renewals_api,
        "get_settings",
        lambda: SimpleNamespace(deployment_read_only=True),
    )

    def reject_persistence(*_args, **_kwargs):
        raise AssertionError("read-only preview must not persist")

    monkeypatch.setattr(renewals_api, "save_preview", reject_persistence)
    result = run(
        renewals_api.import_preview(
            UploadFile(filename="renewals.xlsx", file=BytesIO(b"renewals")),
            UploadFile(filename="master.xlsx", file=BytesIO(b"master")),
            user={"id": 1},
        )
    )

    assert result["data"]["batch_id"] is None
    assert result["data"]["persisted"] is False
    assert result["data"]["summary"]["matched"] == 1
    assert len(result["data"]["review_rows"]) == 1
    assert "raw" not in result["data"]["review_rows"][0]
    assert "member_id" not in result["data"]["review_rows"][0]


def test_preview_result_view_returns_complete_review_queue_and_redacts_raw() -> None:
    rows = []
    for index in range(12):
        rows.append(
            {
                "row_no": index + 2,
                "name": f"复核学长{index}",
                "center_name": "吴江分中心",
                "class_name": "吴越一班",
                "due_month": 7,
                "match_status": "NEEDS_REVIEW",
                "issue_code": "MEMBER_NOT_MATCHED",
                "proposed_status": "PENDING_FIRST_CONTACT",
                "history_note": "",
                "assistance_note": "需要协助" if index == 0 else "",
                "member_id": 100 + index,
                "raw": {"手机号": f"13800138{index:03d}"},
            }
        )
    result = preview_result_view({"summary": {"total": 12}, "rows": rows})
    assert len(result["review_rows"]) == 12
    assert len(result["assistance_rows"]) == 1
    assert result["issue_summary"] == {"MEMBER_NOT_MATCHED": 12}
    assert all("raw" not in row and "member_id" not in row for row in result["review_rows"])


def _member_for_renewal_test() -> dict:
    member = fetch_one("SELECT id, org_unit_id FROM members ORDER BY id LIMIT 1")
    if member:
        return member
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin is not None
    member_id = create_member(
        admin["id"],
        member_code="RENEWAL-TEST-SEED",
        name="续费测试学长",
        org_unit_id="org-suzhou",
        development_org_unit_id=None,
        phone="13900139001",
    )
    return fetch_one("SELECT id, org_unit_id FROM members WHERE id=?", (member_id,))


def test_list_cycles_defaults_to_remaining_unrenewed_and_supports_filters() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin is not None
    now = datetime.now().isoformat()
    year = datetime.now().year
    suffix = f"{uuid4().int % 100000000:08d}"
    org_id = "org-renewal-filter"
    with transaction() as connection:
        execute(
            connection,
            "INSERT OR IGNORE INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
            (org_id, "RENEWAL_FILTER", "续费筛选分中心", now, now),
        )
    member_id = create_member(
        admin["id"],
        member_code=f"RENEWAL-FILTER-{year}-{suffix}",
        name="续费筛选张三",
        org_unit_id=org_id,
        development_org_unit_id=None,
        phone=f"139{suffix}",
    )
    future_member_id = create_member(
        admin["id"],
        member_code=f"RENEWAL-FILTER-FUTURE-{year}-{suffix}",
        name="续费筛选李四",
        org_unit_id=org_id,
        development_org_unit_id=None,
        phone=f"138{suffix}",
    )
    renewed_member_id = create_member(
        admin["id"],
        member_code=f"RENEWAL-FILTER-RENEWED-{year}-{suffix}",
        name="续费筛选王五",
        org_unit_id=org_id,
        development_org_unit_id=None,
        phone=f"137{suffix}",
    )
    with transaction() as connection:
        for member, due_month, status, org_unit in [
            (member_id, 7, "IN_COMMUNICATION", org_id),
            (future_member_id, 9, "PENDING_FIRST_CONTACT", org_id),
            (renewed_member_id, 10, "RENEWED", org_id),
        ]:
            execute(
                connection,
                "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (member, year, org_unit, due_month, status, now, now),
            )

    default_rows = list_cycles(admin["id"], year)
    assert [row["due_month"] for row in default_rows] == [9]
    assert default_rows[0]["member_name"] == "续费筛选李四"

    past_rows = list_cycles(
        admin["id"],
        year,
        org_unit_id=org_id,
        due_month=7,
        member_name="张三",
        renewal_status="ALL",
    )
    assert len(past_rows) == 1
    assert past_rows[0]["status"] == "IN_COMMUNICATION"

    renewed_rows = list_cycles(admin["id"], year, renewal_status="RENEWED")
    assert [row["status"] for row in renewed_rows] == ["RENEWED"]

    all_rows = list_cycles(admin["id"], year, renewal_status="ALL", include_past=True)
    assert {row["due_month"] for row in all_rows} == {7, 9, 10}


def test_renewal_ledger_reads_current_member_management_profile() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin is not None
    now = datetime.now().isoformat()
    suffix = f"{uuid4().int % 100000000:08d}"
    primary_org = f"org-renewal-source-primary-{suffix}"
    development_org = f"org-renewal-source-development-{suffix}"
    with transaction() as connection:
        for org_id, code, name in [
            (primary_org, f"RENEWAL_SOURCE_PRIMARY_{suffix}", "续费主归属测试中心"),
            (development_org, f"RENEWAL_SOURCE_DEVELOPMENT_{suffix}", "学员管理发展中心"),
        ]:
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                (org_id, code, name, now, now),
            )
    member_id = create_member(
        admin["id"],
        member_code=f"RENEWAL-SOURCE-{suffix}",
        name="原始姓名",
        org_unit_id=primary_org,
        development_org_unit_id=None,
        phone=f"139{suffix}",
    )
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, 'IN_COMMUNICATION', ?, ?)",
            (member_id, datetime.now().year, primary_org, now, now),
        )

    update_member(
        admin["id"],
        member_id,
        {"name": "数据库维护后姓名", "development_org_unit_id": development_org},
    )

    rows = list_cycles(
        admin["id"],
        datetime.now().year,
        org_unit_id=development_org,
        member_name="数据库维护后姓名",
        renewal_status="ALL",
        include_past=True,
    )
    assert len(rows) == 1
    assert rows[0]["member_name"] == "数据库维护后姓名"
    assert rows[0]["org_unit_id"] == development_org
    assert rows[0]["org_name"] == "学员管理发展中心"
    assert rows[0]["imported_org_unit_id"] == primary_org

    overview = list_overview(admin["id"], datetime.now().year)
    assert any(
        row["org_unit_id"] == development_org and row["count"] >= 1
        for row in overview["rows"]
    )

    timeline = get_member_timeline(member_id, admin["id"])
    assert any(
        item["event_type"] == "RENEWAL_CYCLE" for item in timeline["events"]
    )
    assert any(
        item["code"] == "RENEWAL_DUE" for item in timeline["service_signals"]
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


def test_apply_preview_imports_only_confirmed_production_links() -> None:
    member = _member_for_renewal_test()
    now = datetime.now().isoformat()
    with transaction() as connection:
        batch_id = execute(
            connection,
            "INSERT INTO renewal_import_batches(source_name, source_sha256, status, preview_json, created_by, created_at) "
            "VALUES ('renewal-safety-test.xlsx', ?, 'PREVIEWED', '{}', 1, ?)",
            ("f" * 64, now),
        ).lastrowid
        rows = [
            (2, "MASTER_PHONE_EXACT", member["id"], 7, "IN_COMMUNICATION", None),
            (3, "NEEDS_REVIEW", member["id"], 8, "NOT_RENEWING", "MASTER_PHONE_DUPLICATE"),
            (4, "MASTER_NAME_CENTER_EXACT", None, 9, "PENDING_FIRST_CONTACT", None),
        ]
        for row_no, match_status, member_id, due_month, status, issue_code in rows:
            execute(
                connection,
                "INSERT INTO renewal_import_staging("
                "batch_id,row_no,match_status,member_id,org_unit_id,due_month,"
                "proposed_status,raw_json,issue_code,created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)",
                (
                    batch_id,
                    row_no,
                    match_status,
                    member_id,
                    member["org_unit_id"],
                    due_month,
                    status,
                    issue_code,
                    now,
                ),
            )

    result = apply_preview(batch_id, 1, 2097, "确认正式导入续费周期")

    assert result == {"created": 1, "updated": 0, "skipped": 2}
    cycle = fetch_one(
        "SELECT due_month, status FROM renewal_cycles WHERE member_id=? AND renewal_year=2097",
        (member["id"],),
    )
    assert cycle == {"due_month": 7, "status": "IN_COMMUNICATION"}

    rollback = rollback_import(batch_id, 1, "确认回滚续费导入批次")
    assert rollback == {"deleted_cycles": 1}
    assert fetch_one(
        "SELECT id FROM renewal_cycles WHERE member_id=? AND renewal_year=2097",
        (member["id"],),
    ) is None
    assert fetch_one(
        "SELECT status FROM renewal_import_batches WHERE id=?", (batch_id,)
    )["status"] == "ROLLED_BACK"


def test_apply_preview_uses_member_management_development_org() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin is not None
    now = datetime.now().isoformat()
    suffix = f"{uuid4().int % 100000000:08d}"
    primary_org = f"org-renewal-import-primary-{suffix}"
    development_org = f"org-renewal-import-development-{suffix}"
    with transaction() as connection:
        for org_id, code, name in [
            (primary_org, f"RENEWAL_IMPORT_PRIMARY_{suffix}", "Excel旧分中心"),
            (development_org, f"RENEWAL_IMPORT_DEVELOPMENT_{suffix}", "数据库发展分中心"),
        ]:
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                (org_id, code, name, now, now),
            )
    member_id = create_member(
        admin["id"],
        member_code=f"RENEWAL-IMPORT-SOURCE-{suffix}",
        name="导入来源学员",
        org_unit_id=primary_org,
        development_org_unit_id=development_org,
        phone=f"138{suffix}",
    )
    with transaction() as connection:
        batch_id = execute(
            connection,
            "INSERT INTO renewal_import_batches(source_name, source_sha256, status, preview_json, created_by, created_at) "
            "VALUES ('initial-renewal.xlsx', ?, 'PREVIEWED', '{}', ?, ?)",
            ("a" * 64, admin["id"], now),
        ).lastrowid
        execute(
            connection,
            "INSERT INTO renewal_import_staging(batch_id, row_no, match_status, member_id, org_unit_id, due_month, proposed_status, raw_json, created_at) "
            "VALUES (?, 2, 'MASTER_PHONE_EXACT', ?, ?, 8, 'IN_COMMUNICATION', '{}', ?)",
            (batch_id, member_id, primary_org, now),
        )

    renewal_year = 2098 + uuid4().int % 10
    result = apply_preview(batch_id, admin["id"], renewal_year, "确认正式导入续费周期")
    assert result == {"created": 1, "updated": 0, "skipped": 0}
    cycle = fetch_one(
        "SELECT org_unit_id FROM renewal_cycles WHERE member_id=? AND renewal_year=?",
        (member_id, renewal_year),
    )
    assert cycle["org_unit_id"] == development_org
    rollback_import(batch_id, admin["id"], "确认回滚续费导入批次")


def test_apply_preview_refuses_to_overwrite_existing_year_cycle() -> None:
    member = _member_for_renewal_test()
    now = datetime.now().isoformat()
    with transaction() as connection:
        execute(
            connection,
            "INSERT OR IGNORE INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, created_at, updated_at) "
            "VALUES (?, 2096, ?, 6, 'PENDING_FIRST_CONTACT', ?, ?)",
            (member["id"], member["org_unit_id"], now, now),
        )
        batch_id = execute(
            connection,
            "INSERT INTO renewal_import_batches(source_name, source_sha256, status, preview_json, created_by, created_at) "
            "VALUES ('renewal-overwrite-test.xlsx', ?, 'PREVIEWED', '{}', 1, ?)",
            ("e" * 64, now),
        ).lastrowid
        execute(
            connection,
            "INSERT INTO renewal_import_staging("
            "batch_id,row_no,match_status,member_id,org_unit_id,due_month,proposed_status,raw_json,created_at) "
            "VALUES (?, 2, 'MASTER_PHONE_EXACT', ?, ?, 7, 'IN_COMMUNICATION', '{}', ?)",
            (batch_id, member["id"], member["org_unit_id"], now),
        )

    with pytest.raises(ValueError, match="目标年度已存在续费周期"):
        apply_preview(batch_id, 1, 2096, "确认正式导入续费周期")
    assert fetch_one(
        "SELECT due_month, status FROM renewal_cycles WHERE member_id=? AND renewal_year=2096",
        (member["id"],),
    ) == {"due_month": 6, "status": "PENDING_FIRST_CONTACT"}


def test_renewal_cycle_followup_and_status_update() -> None:
    member = _member_for_renewal_test()
    now = datetime.now().isoformat()
    with transaction() as connection:
        cycle_id = execute(
            connection,
            "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, created_at, updated_at) "
            "VALUES (?, 2099, ?, 7, 'PENDING_FIRST_CONTACT', ?, ?)",
            (member["id"], member["org_unit_id"], now, now),
        ).lastrowid
        execute(
            connection,
            "INSERT OR IGNORE INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES ('org-renewal-other', 'RENEWAL_OTHER', '续费测试异地分中心', 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
            (now, now),
        )
    followup_id = add_followup(
        cycle_id,
        1,
        channel="PHONE",
        summary="已完成首次续费联系",
        intention="继续沟通",
    )
    update_cycle(cycle_id, 1, status="IN_COMMUNICATION", assigned_user_id=1)
    followups = list_followups(cycle_id, 1)
    assert followups[0]["id"] == followup_id
    cycle = fetch_one(
        "SELECT status, assigned_user_id FROM renewal_cycles WHERE id=?", (cycle_id,)
    )
    assert cycle["status"] == "IN_COMMUNICATION"
    assert cycle["assigned_user_id"] == 1
    audit = fetch_one(
        "SELECT after_json FROM audit_logs WHERE action='renewals.cycle.update' "
        "AND resource_id=? ORDER BY id DESC LIMIT 1",
        (str(cycle_id),),
    )
    assert json.loads(audit["after_json"])["assigned_user_id"] == 1
    update_cycle(cycle_id, 1, status="RENEWED")
    assert fetch_one(
        "SELECT completed_at FROM renewal_cycles WHERE id=?", (cycle_id,)
    )["completed_at"] is not None
    update_cycle(cycle_id, 1, status="IN_COMMUNICATION")
    assert fetch_one(
        "SELECT completed_at FROM renewal_cycles WHERE id=?", (cycle_id,)
    )["completed_at"] is None
    with pytest.raises(ValueError, match="不能超过64个字符"):
        update_cycle(cycle_id, 1, result="超长结果" * 20)


def test_renewal_assignee_requires_manage_permission_and_matching_scope() -> None:
    member = _member_for_renewal_test()
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin is not None
    now = datetime.now().isoformat()
    with transaction() as connection:
        cycle_id = execute(
            connection,
            "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, created_at, updated_at) "
            "VALUES (?, 2098, ?, 7, 'PENDING_FIRST_CONTACT', ?, ?)",
            (member["id"], member["org_unit_id"], now, now),
        ).lastrowid
    read_only_assignee = create_user(
        admin["id"],
        username="renewal-read-only-assignee",
        display_name="续费只读候选",
        password="renewal-read-only-password",
        roles=["read_only"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": "org-suzhou"}],
    )
    with pytest.raises(ValueError, match="续费运营权限"):
        update_cycle(cycle_id, admin["id"], assigned_user_id=read_only_assignee)
    out_of_scope_assignee = create_user(
        admin["id"],
        username="renewal-out-of-scope-assignee",
        display_name="续费异地候选",
        password="renewal-out-of-scope-password",
        roles=["regional_manager"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": "org-renewal-other"}],
    )
    with pytest.raises(ValueError, match="不在续费归属组织范围"):
        update_cycle(cycle_id, admin["id"], assigned_user_id=out_of_scope_assignee)
    candidates = list_assignees(admin["id"], member["org_unit_id"])
    assert admin["id"] in {candidate["id"] for candidate in candidates}
    assert read_only_assignee not in {candidate["id"] for candidate in candidates}
    assert out_of_scope_assignee not in {candidate["id"] for candidate in candidates}
