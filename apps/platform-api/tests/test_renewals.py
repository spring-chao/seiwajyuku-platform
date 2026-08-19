from __future__ import annotations

import json
import tempfile
from asyncio import run
from datetime import UTC, date, datetime
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
    create_cycle_from_member,
    determine_renewal_stage,
    get_action_card,
    list_assignees,
    list_cycle_coverage,
    list_cycles,
    list_followups,
    list_overview,
    preview_result_view,
    rollback_import,
    save_preview,
    update_cycle,
)


@pytest.mark.parametrize(
    ("renewal_year", "due_month", "as_of", "status", "expected"),
    [
        (2026, 12, date(2026, 8, 19), "PENDING_FIRST_CONTACT", "PREPARE"),
        (2026, 11, date(2026, 8, 19), "PENDING_FIRST_CONTACT", "OBSERVE_3"),
        (2026, 10, date(2026, 8, 19), "IN_COMMUNICATION", "RENEW_2"),
        (2026, 9, date(2026, 8, 19), "IN_COMMUNICATION", "FOLLOW_1"),
        (2026, 8, date(2026, 8, 19), "IN_COMMUNICATION", "DUE_NOW"),
        (2026, 7, date(2026, 8, 19), "DEFERRED", "RECOVERY"),
        (2027, 2, date(2026, 11, 30), "PENDING_FIRST_CONTACT", "OBSERVE_3"),
        (2026, 11, date(2026, 8, 19), "RENEWED", "CLOSED"),
        (2026, 11, date(2026, 8, 19), "NOT_RENEWING", "CLOSED"),
        (2026, 11, date(2026, 8, 19), "EXITED", "CLOSED"),
    ],
)
def test_renewal_stage_engine_is_calendar_based_and_closed_status_wins(
    renewal_year: int,
    due_month: int,
    as_of: date,
    status: str,
    expected: str,
) -> None:
    stage = determine_renewal_stage(
        renewal_year, due_month, status, as_of=as_of
    )
    assert stage["code"] == expected


def _action_card_fixture(*, with_memory: bool = True) -> tuple[int, int, str, int]:
    # Preserve the module's shared renewal seed before adding scoped fixtures;
    # several legacy tests intentionally reuse the first member row.
    if not fetch_one("SELECT id FROM members ORDER BY id LIMIT 1"):
        seed_now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            execute(
                connection,
                "INSERT OR IGNORE INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES ('org-renewal-seed', 'RENEWAL_SEED', '续费测试分中心', 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                (seed_now, seed_now),
            )
            execute(
                connection,
                "INSERT INTO members(member_code, name, org_unit_id, status, created_at, updated_at) "
                "VALUES ('RENEWAL-TEST-SEED', '续费测试学长', ?, 'ACTIVE', ?, ?)",
                ("org-renewal-seed", seed_now, seed_now),
            )
    suffix = uuid4().hex[:8]
    center_id = f"renewal-action-center-{suffix}"
    class_id = f"renewal-action-class-{suffix}"
    group_id = f"renewal-action-group-{suffix}"
    now = datetime.now(UTC).isoformat()
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin is not None
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, '行动卡测试分中心', 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
            (center_id, f"ACTION_CENTER_{suffix}", now, now),
        )
        for org_id, code, name, unit_type in (
            (class_id, f"ACTION_CLASS_{suffix}", "行动卡测试班", "CLASS"),
            (group_id, f"ACTION_GROUP_{suffix}", "行动卡测试组", "GROUP"),
        ):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (org_id, code, name, unit_type, center_id, now, now),
            )
        member_id = execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, join_date, study_start_date, created_at, updated_at) "
            "VALUES (?, '行动卡测试学长', ?, 'ACTIVE', '2095-03-18', '2095-03-18', ?, ?)",
            (f"ACTION_MEMBER_{suffix}", center_id, now, now),
        ).lastrowid
        for relation_type, org_id in (
            ("PRIMARY_REGION", center_id),
            ("STUDY_CLASS", class_id),
            ("STUDY_GROUP", group_id),
        ):
            execute(
                connection,
                "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, source_type, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, 'TEST', ?, ?)",
                (member_id, org_id, relation_type, now, now),
            )
        cycle_id = execute(
            connection,
            "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, assigned_user_id, created_at, updated_at) "
            "VALUES (?, 2099, ?, 11, 'IN_COMMUNICATION', ?, ?, ?)",
            (member_id, center_id, admin["id"], now, now),
        ).lastrowid
        execute(
            connection,
            "INSERT INTO renewal_followups(renewal_cycle_id, followed_at, followed_by, channel, summary, intention, needs_support, next_action, next_followup_at, created_at) "
            "VALUES (?, ?, ?, 'WECHAT', '联系电话138-0013-8000，资金安排为人民币1,000,000元，另有五十万元，最近比较忙', '较高', 0, '9月20日再次联系', '2099-09-20', ?)",
            (cycle_id, now, admin["id"], now),
        )
        if with_memory:
            batch_id = execute(
                connection,
                "INSERT INTO import_batches(import_type, source_name, source_sha256, status, preview_json, created_by, created_at) "
                "VALUES ('LEGACY_ACTIVITY', 'test', ?, 'APPLIED', '{}', ?, ?)",
                (f"sha-{suffix}", admin["id"], now),
            ).lastrowid
            execute(
                connection,
                "INSERT INTO member_activity_facts(source_system, source_table, external_id, member_id, org_unit_id, activity_type, occurred_on, participation_status, title, import_batch_id, imported_at) "
                "VALUES ('TEST', 'activity', ?, ?, ?, 'REPORT_MEETING', '2099-06-18', 'PRESENT', '六月经营报告会', ?, ?)",
                (f"activity-{suffix}", member_id, center_id, batch_id, now),
            )
    allowed_user_id = create_user(
        admin["id"],
        username=f"renewal-action-user-{suffix}",
        display_name="续费行动卡测试",
        password="renewal-action-test-password",
        roles=["ops_center_operations"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": center_id}],
    )
    return int(cycle_id), allowed_user_id, center_id, int(member_id)


def test_action_card_uses_verified_memory_redacts_sensitive_values_and_audits() -> None:
    cycle_id, user_id, center_id, _ = _action_card_fixture()
    card = get_action_card(cycle_id, user_id, as_of=date(2099, 8, 19))

    assert card["stage"]["code"] == "OBSERVE_3"
    assert card["member"]["org_unit_id"] == center_id
    assert card["member"]["class_name"] == "行动卡测试班"
    assert card["member"]["group_name"] == "行动卡测试组"
    assert card["member"]["membership_years"] == 4
    assert card["verified_memories"][0]["title"] == "六月经营报告会"
    assert card["verified_memories"][0]["verified"] is True
    assert card["current_context"]["intention"] == "较高"
    serialized = json.dumps(card, ensure_ascii=False)
    assert "138-0013-8000" not in serialized
    assert "1,000,000元" not in serialized
    assert "五十万元" not in serialized
    outbound = " ".join(
        filter(
            None,
            [
                card["action"]["wechat_reference"],
                card["action"]["phone_opening_reference"],
            ],
        )
    )
    assert all(term not in outbound for term in ("缴费", "付款", "续不续", "缴费链接"))
    audit = fetch_one(
        "SELECT after_json FROM audit_logs WHERE action='renewals.action_card.view' "
        "AND resource_id=? ORDER BY id DESC LIMIT 1",
        (str(cycle_id),),
    )
    assert audit is not None
    assert json.loads(audit["after_json"])["stage"] == "OBSERVE_3"


def test_action_card_falls_back_without_inventing_activity_facts() -> None:
    cycle_id, user_id, _, _ = _action_card_fixture(with_memory=False)
    card = get_action_card(cycle_id, user_id, as_of=date(2099, 8, 19))
    assert card["verified_memories"] == []
    assert card["data_quality"]["memory_fallback_used"] is True
    assert "有一段时间没和您细聊了" in card["action"]["wechat_reference"]
    assert "报告会" not in card["action"]["wechat_reference"]


def test_action_card_out_of_scope_becomes_http_403() -> None:
    cycle_id, _, _, _ = _action_card_fixture()
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin is not None
    suffix = uuid4().hex[:8]
    other_center = f"renewal-action-other-{suffix}"
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, '其他测试分中心', 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
            (other_center, f"ACTION_OTHER_{suffix}", now, now),
        )
    denied_user_id = create_user(
        admin["id"],
        username=f"renewal-action-denied-{suffix}",
        display_name="续费行动卡越权测试",
        password="renewal-action-test-password",
        roles=["ops_center_operations"],
        scopes=[{"scope_type": "UNIT", "org_unit_id": other_center}],
    )
    with pytest.raises(renewals_api.HTTPException) as exc:
        renewals_api.action_card(cycle_id, user={"id": denied_user_id})
    assert exc.value.status_code == 403


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

    renewed_rows = list_cycles(
        admin["id"], year, org_unit_id=org_id, renewal_status="RENEWED"
    )
    assert [row["status"] for row in renewed_rows] == ["RENEWED"]

    all_rows = list_cycles(
        admin["id"],
        year,
        org_unit_id=org_id,
        renewal_status="ALL",
        include_past=True,
    )
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


def test_cycle_coverage_exposes_member_master_gaps_instead_of_hiding_them() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin is not None
    now = datetime.now().isoformat()
    year = datetime.now().year
    suffix = f"{uuid4().int % 100000000:08d}"
    org_id = f"org-renewal-coverage-{suffix}"
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
            "is_active, created_at, updated_at) VALUES (?, ?, ?, "
            "'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
            (org_id, f"RENEWAL_COVERAGE_{suffix}", "续费覆盖测试中心", now, now),
        )
    member_ids = {
        "synced": create_member(
            admin["id"],
            member_code=f"RENEWAL-COVERAGE-SYNCED-{suffix}",
            name=f"覆盖已同步{suffix}",
            org_unit_id=org_id,
            development_org_unit_id=None,
            phone=f"139{suffix}",
            renewal_month=f"{year}-08",
        ),
        "ready": create_member(
            admin["id"],
            member_code=f"RENEWAL-COVERAGE-READY-{suffix}",
            name=f"覆盖待建立{suffix}",
            org_unit_id=org_id,
            development_org_unit_id=None,
            phone=f"138{suffix}",
            renewal_month=f"{year - 1}-09",
        ),
        "missing": create_member(
            admin["id"],
            member_code=f"RENEWAL-COVERAGE-MISSING-{suffix}",
            name=f"覆盖缺月份{suffix}",
            org_unit_id=org_id,
            development_org_unit_id=None,
            phone=f"137{suffix}",
        ),
        "inactive": create_member(
            admin["id"],
            member_code=f"RENEWAL-COVERAGE-INACTIVE-{suffix}",
            name=f"覆盖已停用{suffix}",
            org_unit_id=org_id,
            development_org_unit_id=None,
            phone=f"136{suffix}",
            renewal_month=f"{year}-10",
            status="INACTIVE",
        ),
        "suspended": create_member(
            admin["id"],
            member_code=f"RENEWAL-COVERAGE-SUSPENDED-{suffix}",
            name=f"覆盖已暂停{suffix}",
            org_unit_id=org_id,
            development_org_unit_id=None,
            phone=f"134{suffix}",
            renewal_month=f"{year}-11",
            status="SUSPENDED",
        ),
    }
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, "
            "status, created_at, updated_at) VALUES (?, ?, ?, 8, "
            "'PENDING_FIRST_CONTACT', ?, ?)",
            (member_ids["synced"], year, org_id, now, now),
        )

    coverage = list_cycle_coverage(
        admin["id"],
        year,
        org_unit_id=org_id,
        member_name="覆盖",
        include_synced=True,
        actionable_only=False,
    )

    assert coverage["summary"] == {
        "member_total": 5,
        "active_member_total": 3,
        "cycle_total": 1,
        "ready_to_create_count": 1,
        "missing_renewal_month_count": 1,
        "inactive_member_count": 1,
        "suspended_member_count": 1,
    }
    by_member_id = {row["member_id"]: row for row in coverage["rows"]}
    assert by_member_id[member_ids["synced"]]["sync_status"] == "SYNCED"
    assert by_member_id[member_ids["ready"]]["sync_status"] == "READY_TO_CREATE"
    assert by_member_id[member_ids["ready"]]["due_month"] == 9
    assert by_member_id[member_ids["missing"]]["sync_status"] == "MISSING_RENEWAL_MONTH"
    assert by_member_id[member_ids["inactive"]]["sync_status"] == "INACTIVE"
    assert by_member_id[member_ids["suspended"]]["sync_status"] == "SUSPENDED"

    actionable = list_cycle_coverage(
        admin["id"],
        year,
        org_unit_id=org_id,
        member_name="覆盖",
        include_synced=True,
    )
    assert {row["sync_status"] for row in actionable["rows"]} == {
        "READY_TO_CREATE",
        "MISSING_RENEWAL_MONTH",
    }
    assert actionable["summary"] == coverage["summary"]

    scoped_user_id = create_user(
        admin["id"],
        username=f"renewal-coverage-scoped-{suffix}",
        display_name="续费覆盖范围测试",
        password=f"test-{uuid4().hex}",
        roles=["regional_manager"],
        scopes=[{"scope_type": "UNIT", "org_unit_id": "org-suzhou"}],
    )
    scoped_coverage = list_cycle_coverage(
        scoped_user_id,
        year,
        org_unit_id=org_id,
        member_name="覆盖",
        include_synced=True,
    )
    assert scoped_coverage["summary"]["member_total"] == 0
    assert scoped_coverage["rows"] == []


def test_create_cycle_from_member_is_single_record_audited_and_idempotent() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin is not None
    now = datetime.now().isoformat()
    year = datetime.now().year
    suffix = f"{uuid4().int % 100000000:08d}"
    primary_org = f"org-renewal-create-primary-{suffix}"
    development_org = f"org-renewal-create-development-{suffix}"
    with transaction() as connection:
        for org_id, code, name in [
            (primary_org, f"RENEWAL_CREATE_PRIMARY_{suffix}", "续费建立主归属"),
            (
                development_org,
                f"RENEWAL_CREATE_DEVELOPMENT_{suffix}",
                "续费建立发展归属",
            ),
        ]:
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
                "is_active, created_at, updated_at) VALUES (?, ?, ?, "
                "'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                (org_id, code, name, now, now),
            )
    member_id = create_member(
        admin["id"],
        member_code=f"RENEWAL-CREATE-{suffix}",
        name="续费单条建立测试",
        org_unit_id=primary_org,
        development_org_unit_id=development_org,
        phone=f"135{suffix}",
        renewal_month=f"{year - 1}-11",
    )

    cycle_id = create_cycle_from_member(
        member_id,
        admin["id"],
        renewal_year=year,
        confirmation="确认从学员主档建立续费周期",
    )
    cycle = fetch_one("SELECT * FROM renewal_cycles WHERE id=?", (cycle_id,))
    assert cycle is not None
    assert cycle["member_id"] == member_id
    assert cycle["org_unit_id"] == development_org
    assert cycle["due_month"] == 11
    assert cycle["status"] == "PENDING_FIRST_CONTACT"
    audit = fetch_one(
        "SELECT id FROM audit_logs WHERE action='renewals.cycle.create_from_member' "
        "AND resource_id=?",
        (str(cycle_id),),
    )
    assert audit is not None
    with pytest.raises(ValueError, match="本年度续费周期已存在"):
        create_cycle_from_member(
            member_id,
            admin["id"],
            renewal_year=year,
            confirmation="确认从学员主档建立续费周期",
        )


def test_join_date_infers_renewal_month_and_manual_override_is_preserved() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin is not None
    now = datetime.now()
    year = now.year
    suffix = f"{uuid4().int % 100000000:08d}"
    org_id = f"org-renewal-infer-{suffix}"
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
            "is_active, created_at, updated_at) VALUES (?, ?, ?, "
            "'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
            (org_id, f"RENEWAL_INFER_{suffix}", "续费月份自动推导测试中心", now.isoformat(), now.isoformat()),
        )
    member_id = create_member(
        admin["id"],
        member_code=f"RENEWAL-INFER-{suffix}",
        name=f"续费月份自动推导{suffix}",
        org_unit_id=org_id,
        development_org_unit_id=None,
        phone=f"133{suffix}",
        join_date=f"{year}-04-20",
    )

    member = fetch_one(
        "SELECT renewal_month, renewal_month_overridden FROM members WHERE id=?",
        (member_id,),
    )
    assert member == {"renewal_month": f"{year}-04", "renewal_month_overridden": 0}

    update_member(
        admin["id"],
        member_id,
        {"join_date": f"{year}-06-20", "renewal_month_overridden": False},
    )
    updated = fetch_one(
        "SELECT renewal_month, renewal_month_overridden FROM members WHERE id=?",
        (member_id,),
    )
    assert updated == {"renewal_month": f"{year}-06", "renewal_month_overridden": 0}

    update_member(
        admin["id"],
        member_id,
        {"renewal_month": f"{year}-11", "renewal_month_overridden": True},
    )
    update_member(
        admin["id"],
        member_id,
        {"join_date": f"{year}-07-20", "renewal_month_overridden": True},
    )
    manual = fetch_one(
        "SELECT renewal_month, renewal_month_overridden FROM members WHERE id=?",
        (member_id,),
    )
    assert manual == {"renewal_month": f"{year}-11", "renewal_month_overridden": 1}

    historical_cycle = fetch_one(
        "SELECT status, completed_at FROM renewal_cycles WHERE member_id=? AND renewal_year=?",
        (member_id, year),
    )
    if now.month > 4:
        assert historical_cycle is not None
        assert historical_cycle["status"] == "RENEWED"
        assert historical_cycle["completed_at"] is not None


def test_past_renewal_month_maintenance_completes_existing_open_cycle() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin is not None
    now = datetime.now()
    if now.month == 1:
        pytest.skip("当前月份没有可用于测试的历史月份")
    year = now.year
    past_month = now.month - 1
    suffix = f"{uuid4().int % 100000000:08d}"
    org_id = f"org-renewal-existing-{suffix}"
    created_at = now.isoformat()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
            "is_active, created_at, updated_at) VALUES (?, ?, ?, "
            "'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
            (
                org_id,
                f"RENEWAL_EXISTING_{suffix}",
                "历史续费已有周期测试中心",
                created_at,
                created_at,
            ),
        )
    member_id = create_member(
        admin["id"],
        member_code=f"RENEWAL-EXISTING-{suffix}",
        name=f"历史续费已有周期{suffix}",
        org_unit_id=org_id,
        development_org_unit_id=None,
        phone=f"132{suffix}",
    )
    with transaction() as connection:
        cycle_id = execute(
            connection,
            "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, "
            "'CONTACTED_WAITING_REPLY', ?, ?)",
            (member_id, year, org_id, past_month, created_at, created_at),
        ).lastrowid

    update_member(
        admin["id"],
        member_id,
        {
            "renewal_month": f"{year}-{past_month:02d}",
            "renewal_month_overridden": True,
        },
    )

    cycle = fetch_one(
        "SELECT status, due_month, completed_at FROM renewal_cycles WHERE id=?",
        (cycle_id,),
    )
    assert cycle["status"] == "RENEWED"
    assert cycle["due_month"] == past_month
    assert cycle["completed_at"] is not None
    history = fetch_one(
        "SELECT from_status, to_status, reason FROM renewal_status_history "
        "WHERE renewal_cycle_id=? ORDER BY id DESC LIMIT 1",
        (cycle_id,),
    )
    assert history["from_status"] == "CONTACTED_WAITING_REPLY"
    assert history["to_status"] == "RENEWED"
    assert "已有周期自动标记为已续费" in history["reason"]
    audit = fetch_one(
        "SELECT id FROM audit_logs WHERE action='renewals.cycle.auto_complete_historical' "
        "AND resource_id=?",
        (str(cycle_id),),
    )
    assert audit is not None


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
