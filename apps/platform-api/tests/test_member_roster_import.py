from __future__ import annotations

import json
from datetime import datetime, UTC
from io import BytesIO

from openpyxl import Workbook

from app.db import execute, fetch_one, transaction
from app.core.privacy import decrypt_text, encrypt_text
from app.migrations import run_migrations
from app.services.iam import seed_iam
from app.services.member_roster_import import apply_member_roster, preview_member_roster
from app.services.members import create_member


def _workbook_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "26年在册总表"
    sheet.append([
        "序号", "是否在册", "姓名", "性别", "职务", "生日时间", "手机号码",
        "所在分中心", "所属班级", "班组委名称", "所属小组", "公司名称", "入塾日期",
        "学习时间", "推荐人", "员工人数", "26年续费时间", "销售收入",
    ])
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _orgs() -> None:
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        for values in (
            ("import-center", "IMPORT_CENTER", "导入测试分中心", "REGIONAL_CENTER", "org-suzhou"),
            ("import-class", "IMPORT_CLASS", "导入测试班", "CLASS", "import-center"),
            ("import-group", "IMPORT_GROUP", "导入测试组", "GROUP", "import-class"),
        ):
            execute(
                connection,
                "INSERT OR IGNORE INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (*values, now, now),
            )


def setup_module() -> None:
    run_migrations()
    seed_iam()
    _orgs()


def test_preview_is_aggregate_only_and_identifies_existing_and_new_members() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    existing_id = create_member(
        admin["id"],
        member_code="IMPORT-EXISTING",
        name="已存在学员",
        org_unit_id="import-center",
        development_org_unit_id=None,
        phone="15500001000",
    )
    content = _workbook_bytes([
        ["0001", "在册", "已存在学员", "男", "总经理", "1980-01-02", "15500001000", "导入测试分中心", "导入测试班", "一号班组委", "导入测试组", "测试公司", "2020-01-02", "2020-02-01", "推荐人", "10人", "2026-11-01", "100"],
        ["0002", "在册", "新增学员", "女", "经理", "1985-03-04", "15500001001", "导入测试分中心", "导入测试班", "二号班组委", "导入测试组", "新增公司", "2021-03-04", "2021-04-01", "推荐人2", "8人", "2026-12-01", "200"],
    ])
    result = preview_member_roster(content, "latest.xlsx", admin["id"])
    assert result["automatic_production_write_allowed"] is False
    assert result["matching"]["existing_member_count"] == 1
    assert result["matching"]["new_member_count"] == 1
    assert result["matching"]["manual_review_count"] == 0
    assert result["sensitive"]["enterprise_financial_write_allowed"] is True
    assert result["sensitive"]["annual_sales_ready_count"] == 2
    serialized = str(result)
    assert "15500001000" not in serialized
    assert "member_id" not in serialized


def test_apply_only_fills_missing_fields_and_adds_committee_name() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    member_id = create_member(
        admin["id"],
        member_code="IMPORT-UPDATE",
        name="待补充学员",
        org_unit_id="import-center",
        development_org_unit_id=None,
        phone="15500001002",
        company_name="原公司",
    )
    content = _workbook_bytes([
        ["0003", "在册", "待补充学员", "男", "董事", "1981-01-02", "15500001002", "导入测试分中心", "导入测试班", "班组委A", "导入测试组", "新公司不覆盖", "2020-01-02", "2020-02-01", "推荐人", "12人", "2026-11-01", "1200"],
    ])
    result = apply_member_roster(content, "latest.xlsx", admin["id"], "确认补充导入学员主档")
    assert result["status"] == "APPLIED"
    row = fetch_one("SELECT company_name, class_committee_name, position, employee_count FROM members WHERE id=?", (member_id,))
    assert row["company_name"] == "原公司"
    assert row["class_committee_name"] == "班组委A"
    assert row["position"] == "董事"
    assert row["employee_count"] == 12
    assert result["annual_sales_applied"] == 1
    counts = {item["field"]: item["count"] for item in result["field_applied_counts"]}
    assert counts["class_committee_name"] == 1
    assert counts["annual_sales"] == 1
    assert "company_name" not in counts
    assert "enterprise_financial_ciphertext" not in counts
    assert "新公司不覆盖" not in json.dumps(result, ensure_ascii=False)
    assert "15500001002" not in json.dumps(result)
    repeat = apply_member_roster(content, "latest.xlsx", admin["id"], "确认补充导入学员主档")
    assert repeat["status"] == "ALREADY_APPLIED"
    assert repeat["field_applied_counts"] == result["field_applied_counts"]
    assert repeat["updated"] == result["updated"]
    assert preview_member_roster(content, "latest.xlsx", admin["id"])["previous_import"] == repeat
    financial = fetch_one("SELECT enterprise_financial_ciphertext FROM members WHERE id=?", (member_id,))
    assert json.loads(decrypt_text(financial["enterprise_financial_ciphertext"]))["annual_sales"] == "1200"


def test_preview_returns_a_masked_manual_review_list_and_never_overwrites_sales() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    member_id = create_member(
        admin["id"],
        member_code="IMPORT-SALES-EXISTING",
        name="销售已维护学员",
        org_unit_id="import-center",
        development_org_unit_id=None,
        phone="15500001004",
    )
    with transaction() as connection:
        execute(
            connection,
            "UPDATE members SET enterprise_financial_ciphertext=? WHERE id=?",
            (encrypt_text(json.dumps({"annual_sales": "已有收入"}, ensure_ascii=False)), member_id),
        )
    content = _workbook_bytes([
        ["0004", "在册", "重复一", "男", "", "", "15500001003", "导入测试分中心", "", "", "", "", "", "", "", "", "", ""],
        ["0005", "在册", "重复二", "男", "", "", "15500001003", "导入测试分中心", "", "", "", "", "", "", "", "", "", ""],
        ["0006", "在册", "缺号学员", "女", "", "", "", "导入测试分中心", "", "", "", "", "", "", "", "", "", ""],
        ["0007", "在册", "销售已维护学员", "男", "", "", "15500001004", "导入测试分中心", "", "", "", "", "", "", "", "", "", "新的收入不覆盖"],
    ])
    preview = preview_member_roster(content, "latest.xlsx", admin["id"])
    assert preview["matching"]["manual_review_count"] == 3
    assert preview["sensitive"]["annual_sales_ready_count"] == 0
    assert {item["name"] for item in preview["manual_review_items"]} == {"重复一", "重复二", "缺号学员"}
    assert all(item["phone_masked"] != "15500001003" for item in preview["manual_review_items"])
    assert "15500001003" not in str(preview)

    applied = apply_member_roster(content, "latest.xlsx", admin["id"], "确认补充导入学员主档")
    assert applied["annual_sales_applied"] == 0
    stored = fetch_one("SELECT enterprise_financial_ciphertext FROM members WHERE id=?", (member_id,))
    assert json.loads(decrypt_text(stored["enterprise_financial_ciphertext"]))["annual_sales"] == "已有收入"
    assert applied["skipped_manual_review"] == 3
    assert {item["code"] for item in applied["skipped_reasons"]} >= {"DUPLICATE_SOURCE_PHONE", "MISSING_OR_INVALID_PHONE"}


def test_historical_receipt_does_not_disclose_other_actors_or_changed_scopes() -> None:
    from unittest.mock import patch
    from app.services.member_roster_import import _previous_import
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    content = _workbook_bytes([
        ["receipt-scope", "在册", "回执范围测试", "女", "", "", "15500001991", "导入测试分中心", "", "", "", "", "", "", "", "", "", ""],
    ])
    result = apply_member_roster(content, "receipt.xlsx", admin["id"], "确认补充导入学员主档")
    assert _previous_import(result["source_sha256"], admin["id"] + 999999) is None
    with patch("app.services.member_roster_import._scope_fingerprint", return_value="changed"):
        old = _previous_import(result["source_sha256"], admin["id"])
        assert old["status"] == "ALREADY_APPLIED"
        assert "updated" not in old
        assert "field_applied_counts" not in old


def test_failed_import_rolls_back_batch_and_profile_changes() -> None:
    import hashlib
    import pytest
    from unittest.mock import patch
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    content = _workbook_bytes([
        ["receipt-fail", "在册", "回执回滚测试", "女", "", "", "15500001992", "导入测试分中心", "", "", "", "", "", "", "", "", "", ""],
    ])
    with patch("app.services.member_roster_import.write_audit", side_effect=RuntimeError("audit failed")):
        with pytest.raises(RuntimeError, match="audit failed"):
            apply_member_roster(content, "failure.xlsx", admin["id"], "确认补充导入学员主档")
    assert fetch_one("SELECT id FROM import_batches WHERE import_type='MEMBER_PROFILE_SUPPLEMENT' AND source_sha256=?", (hashlib.sha256(content).hexdigest(),)) is None
    assert fetch_one("SELECT id FROM members WHERE name='回执回滚测试'") is None
