from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db import execute, fetch_all, fetch_one, transaction
from app.main import app
from app.services.member_group_reassignment import (
    apply_group_reassignment, group_reassignment_roster, preview_group_reassignment,
)
from test_v12_mvp import _seed_group_leader_fixture


def prepared():
    f = _seed_group_leader_fixture()
    with transaction() as connection:
        execute(connection, "UPDATE members SET org_unit_id=(SELECT parent_id FROM org_units WHERE id=?) WHERE id IN (?,?)",
                (f["class_id"], f["member_id"], f["other_member_id"]))
    actor = fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"]
    payload = {"actor_user_id": actor, "class_org_unit_id": f["class_id"], "assignments": [
        {"member_id": f["member_id"], "group_org_unit_id": f["other_group_id"]},
        {"member_id": f["other_member_id"], "group_org_unit_id": f["group_id"]},
    ]}
    return f, payload


def snapshot(f):
    return {table: fetch_all(f"SELECT * FROM {table} ORDER BY id") for table in
            ("members", "member_org_relations", "member_change_history", "study_meeting_attendances", "learning_credit_entries", "audit_logs")}


def test_preview_no_writes_apply_atomic_and_audited():
    f, payload = prepared()
    before = snapshot(f)
    roster = group_reassignment_roster(actor_user_id=payload["actor_user_id"], class_org_unit_id=f["class_id"])
    assert {item["id"] for item in roster["members"]} == {f["member_id"], f["other_member_id"]}
    preview = preview_group_reassignment(**payload)
    assert preview["changed_count"] == 2
    assert snapshot(f) == before
    result = apply_group_reassignment(**payload, preview_fingerprint=preview["preview_fingerprint"], reason="重新开班，小组重组")
    assert result["changed_count"] == 2
    for item in payload["assignments"]:
        relation = fetch_one("SELECT org_unit_id FROM member_org_relations WHERE member_id=? AND relation_type='STUDY_GROUP'", (item["member_id"],))
        assert relation["org_unit_id"] == item["group_org_unit_id"]
    after = snapshot(f)
    assert after["learning_credit_entries"] == before["learning_credit_entries"]
    assert after["study_meeting_attendances"] == before["study_meeting_attendances"]
    assert len(after["member_change_history"]) == len(before["member_change_history"]) + 2
    audit = fetch_one("SELECT purpose FROM audit_logs WHERE action='members.group_reassignment' AND resource_id=?", (f["class_id"],))
    assert audit["purpose"] == "重新开班，小组重组"
    with pytest.raises(ValueError, match="重新预览"):
        apply_group_reassignment(**payload, preview_fingerprint=preview["preview_fingerprint"], reason="重复保存")


@pytest.mark.parametrize("case", ["foreign_member", "foreign_group", "duplicate", "inactive", "ambiguous", "expired", "stale", "readonly", "audit"])
def test_invalid_or_stale_batches_never_partially_save(case, monkeypatch):
    f, payload = prepared()
    preview = preview_group_reassignment(**payload)
    if case == "foreign_member": payload["assignments"][1]["member_id"] = f["foreign_member_id"]
    if case == "foreign_group": payload["assignments"][1]["group_org_unit_id"] = f["other_class_id"]
    if case == "duplicate": payload["assignments"][1]["member_id"] = f["member_id"]
    if case in {"inactive", "stale", "ambiguous", "expired"}:
        with transaction() as connection:
            if case == "inactive": execute(connection, "UPDATE members SET status='INACTIVE' WHERE id=?", (f["other_member_id"],))
            if case == "stale": execute(connection, "UPDATE members SET updated_at='2000-01-01' WHERE id=?", (f["other_member_id"],))
            if case == "expired": execute(connection, "UPDATE member_org_relations SET valid_until='2000-01-01' WHERE member_id=? AND relation_type='STUDY_CLASS'", (f["other_member_id"],))
            if case == "ambiguous": execute(connection, "INSERT INTO member_org_relations(member_id,org_unit_id,relation_type,is_primary,created_at,updated_at) VALUES (?,?,'STUDY_GROUP',0,'2020-01-01','2020-01-01')", (f["other_member_id"], f["group_id"]))
    if case == "readonly": monkeypatch.setenv("DEPLOYMENT_READ_ONLY", "true")
    before = snapshot(f)
    if case == "audit":
        with patch("app.services.member_group_reassignment.write_audit", side_effect=RuntimeError("audit failed")), pytest.raises(RuntimeError):
            apply_group_reassignment(**payload, preview_fingerprint=preview["preview_fingerprint"], reason="核对重组")
    else:
        with pytest.raises((ValueError, PermissionError)):
            apply_group_reassignment(**payload, preview_fingerprint=preview["preview_fingerprint"], reason="核对重组")
    assert snapshot(f) == before


def test_permission_scope_and_http_payload_validation(monkeypatch):
    f, payload = prepared()
    with patch("app.services.member_group_reassignment.user_context", return_value={"permissions": []}), pytest.raises(PermissionError):
        preview_group_reassignment(**payload)
    with patch("app.services.member_group_reassignment.accessible_org_ids", return_value={f["other_class_id"]}), pytest.raises(PermissionError):
        preview_group_reassignment(**payload)
    user = fetch_one("SELECT id, token_version FROM app_users WHERE username='admin'")
    headers = {"Authorization": "Bearer " + create_token(user["id"], user["token_version"], "access", timedelta(minutes=5))}
    body = {key: value for key, value in payload.items() if key != "actor_user_id"}
    with TestClient(app) as client:
        url = "/api/v1/members/group-reassignment/preview"
        assert client.post(url, json=body).status_code == 401
        assert client.post(url, json={**body, "unexpected": True}, headers=headers).status_code == 422
        assert client.post(url, json=body, headers=headers).status_code == 200
