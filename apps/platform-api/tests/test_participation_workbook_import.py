"""Workbook facts reuse the existing guarded import and stable member codes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.legacy_operations_merge import apply_bundle, parse_bundle, preview_bundle
from app.services.wechat_learning import get_member_learning_summary


def bundle(code: str, external_id: str, source_system: str = "activity_workbooks") -> bytes:
    return json.dumps(
        {
            "bundle_version": 1,
            "source_system": source_system,
            "privacy_contract": {
                "matching_key": "member_code",
                "contains_names": False,
                "contains_phones": False,
                "contains_narratives": False,
            },
            "facts": [
                {
                    "source_table": "learning_meetings",
                    "external_id": external_id,
                    "member_code": code,
                    "occurred_on": "2025-08-18",
                    "participation_status": "PRESENT",
                    "title": "八月学习会",
                },
                {
                    "source_table": "other_activities",
                    "external_id": external_id,
                    "member_code": code,
                    "occurred_on": "2026-08-18",
                    "participation_status": "RECORDED",
                    "title": "新学员见面会",
                },
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")


def test_workbook_parser_does_not_expand_the_legacy_source_contract() -> None:
    parsed = parse_bundle(bundle("SYNTHETIC-CODE", "synthetic-event"))
    assert parsed["source_system"] == "activity_workbooks"
    assert [item["activity_type"] for item in parsed["facts"]] == [
        "LEARNING_MEETING", "OTHER_ACTIVITY"
    ]
    with pytest.raises(ValueError, match="source_table"):
        parse_bundle(bundle("SYNTHETIC-CODE", "synthetic-event", "seiwajyuku_system"))
    payload = json.loads(bundle("SYNTHETIC-CODE", "synthetic-event"))
    payload["source_system"] = ["activity_workbooks"]
    with pytest.raises(ValueError, match="source_system"):
        parse_bundle(json.dumps(payload).encode("utf-8"))


@pytest.mark.parametrize("field,value", [("name", "合成姓名"), ("phone", "prohibited"), ("notes", "原始笔记")])
def test_workbook_import_rejects_identity_and_narrative_fields(field: str, value: str) -> None:
    payload = json.loads(bundle("SYNTHETIC-CODE", "synthetic-event"))
    payload["facts"][0][field] = value
    with pytest.raises(ValueError, match="禁止迁移字段"):
        parse_bundle(json.dumps(payload).encode("utf-8"))


def test_workbook_preview_missing_identity_never_guesses_person() -> None:
    result = preview_bundle(bundle("NO-SUCH-PERSON", uuid4().hex), "workbook.json")
    assert result["summary"]["total"] == 2
    assert result["summary"]["unmatched_member"] == 2
    assert result["summary"]["importable"] == 0
    assert result["privacy"]["matching_key"] == "member_code"
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    with pytest.raises(ValueError, match="先完成人员匹配"):
        apply_bundle(bundle("NO-SUCH-PERSON", uuid4().hex), "workbook.json", admin["id"], "导入尚未匹配的合成人员", True)


def test_workbook_apply_requires_confirmation_and_is_source_scoped_and_idempotent() -> None:
    code = f"WORKBOOK-TEST-{uuid4().hex}"
    external_id = uuid4().hex
    now = datetime.now(UTC).isoformat()
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    with transaction() as connection:
        member_id = execute(
            connection,
            "INSERT INTO members(member_code,name,org_unit_id,status,created_at,updated_at) "
            "VALUES (?, '合成导入学员', 'org-suzhou', 'ACTIVE', ?, ?)",
            (code, now, now),
        ).lastrowid
    content = bundle(code, external_id)
    before = preview_bundle(content, "workbook.json")
    assert before["summary"]["importable"] == 2
    assert not fetch_all("SELECT id FROM member_activity_facts WHERE member_id=?", (member_id,))
    with pytest.raises(ValueError, match="二次确认"):
        apply_bundle(content, "workbook.json", admin["id"], "导入合成历史活动事实", False)
    result = apply_bundle(content, "workbook.json", admin["id"], "导入合成历史活动事实", True)
    assert result["importable"] == 2
    rows = fetch_all("SELECT source_system,activity_type FROM member_activity_facts WHERE member_id=?", (member_id,))
    assert {row["source_system"] for row in rows} == {"activity_workbooks"}
    assert {row["activity_type"] for row in rows} == {"LEARNING_MEETING", "OTHER_ACTIVITY"}
    summary = get_member_learning_summary(member_id)
    assert [item["title"] for item in summary["recent_learning"]] == ["八月学习会"]
    after = preview_bundle(content, "workbook.json")
    assert after["summary"]["duplicates"] == 2
    assert after["summary"]["importable"] == 0
    with pytest.raises(ValueError, match="已经执行"):
        apply_bundle(content, "workbook.json", admin["id"], "再次导入合成历史活动", True)
    audit = fetch_one(
        "SELECT action FROM audit_logs WHERE resource_type='import_batch' AND resource_id=?",
        (str(result["batch_id"]),),
    )
    assert audit["action"] == "legacy_operations.merge.apply"
