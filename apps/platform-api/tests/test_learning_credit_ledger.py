from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db import execute, fetch_one, transaction
from app.main import app
from app.services.learning_credits import (
    LearningCreditError,
    dry_run_study_meeting_settlement,
    member_credit_summary,
    post_credit_entry,
    reverse_credit_entry,
    settle_study_meeting,
)
from app.services.credit_settlement_batches import (
    approve_study_meeting_batch,
    dry_run_study_meeting_batch,
    post_study_meeting_batch,
    submit_study_meeting_batch_for_approval,
)
from app.services.study_meetings import (
    confirm_study_meeting_course_completion,
)
from app.services.wechat_identity import verify_member_binding
from test_study_meeting_evidence import create, photo
from test_v12_mvp import _seed_group_leader_fixture


@pytest.fixture(autouse=True)
def credit_test_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STUDY_MEETING_SUBMISSION_ENABLED", "true")
    monkeypatch.setenv("STUDY_MEETING_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("STUDY_MEETING_COURSE_EDIT_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")
    monkeypatch.setenv("STUDY_EVIDENCE_LOCAL_ROOT", str(tmp_path))


def _admin_id() -> int:
    return int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])


def _use_credit_plan(f: dict, course_key: str | None = None) -> None:
    binding = fetch_one(
        "SELECT b.id, p.plan_key, p.version_label FROM class_learning_bindings b "
        "JOIN learning_plan_versions p ON p.id=b.plan_version_id WHERE b.class_org_unit_id=?",
        (f["class_id"],),
    )
    assert binding
    with transaction() as connection:
        generic_cursor = execute(
            connection,
            "INSERT INTO learning_credit_rule_versions "
            "(rule_set_key, version_label, status, created_at, updated_at) "
            "VALUES (?, ?, 'PUBLISHED', ?, ?)",
            (binding["plan_key"], binding["version_label"], "2026-09-06", "2026-09-06"),
        )
        generic_id = int(generic_cursor.lastrowid)
        execute(
            connection,
            "INSERT INTO learning_credit_rules "
            "(rule_version_id, rule_key, credit_category, credit_type, settlement_model, points, rule_snapshot_json, created_at, updated_at) "
            "VALUES (?, 'GROUP_MEETING_ATTENDANCE', 'STANDARD_LEARNING', 'GROUP_MEETING_ATTENDANCE', 'CYCLE_ONCE', 4, '{}', ?, ?)",
            (generic_id, "2026-09-06", "2026-09-06"),
        )
        execute(
            connection,
            "INSERT INTO learning_credit_rules "
            "(rule_version_id, rule_key, credit_category, credit_type, settlement_model, rule_snapshot_json, created_at, updated_at) "
            "VALUES (?, 'COURSE_COMPLETION', 'STANDARD_LEARNING', 'COURSE_COMPLETION', 'COURSE_COMPLETION', '{}', ?, ?)",
            (generic_id, "2026-09-06", "2026-09-06"),
        )
        execute(
            connection,
            "UPDATE class_learning_bindings SET credit_rule_version_id=? "
            "WHERE class_org_unit_id=?",
            (generic_id, f["class_id"]),
        )
        if course_key:
            course_cursor = execute(
                connection,
                "INSERT INTO learning_plan_credit_rule_versions "
                "(plan_key, version_label, status, created_at, updated_at) VALUES (?, ?, 'DRAFT', ?, ?)",
                (binding["plan_key"], binding["version_label"], "2026-09-06", "2026-09-06"),
            )
            rule_id = int(course_cursor.lastrowid)
            execute(
                connection,
                "UPDATE class_learning_bindings SET course_credit_rule_version_id=? "
                "WHERE class_org_unit_id=?",
                (rule_id, f["class_id"]),
            )
            execute(
                connection,
                "INSERT INTO learning_plan_credit_rules "
                "(rule_version_id, course_key, course_name, year_index, credit_points, status, source, aliases_json, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, 40, 'CONFIGURED', 'BASELINE', '[]', ?, ?)",
                (rule_id, course_key, course_key, "2026-09-06", "2026-09-06"),
            )


def _submit_without_evidence(session: dict) -> None:
    with transaction() as connection:
        execute(
            connection,
            "UPDATE study_meeting_sessions SET status='SUBMITTED', submitted_at=updated_at "
            "WHERE id=?",
            (session["id"],),
        )


def test_group_meeting_dry_run_is_cycle_once_and_does_not_write() -> None:
    f = _seed_group_leader_fixture()
    _use_credit_plan(f)
    session = create(f)
    _submit_without_evidence(session)

    before = fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]
    preview = dry_run_study_meeting_settlement(
        actor_user_id=_admin_id(), session_id=session["id"]
    )

    assert preview["mode"] == "DRY_RUN"
    assert preview["persisted"] is False
    assert preview["totals"]["proposed_points"] == 8
    assert {item["idempotency_key"] for item in preview["entries"]} == {
        f"GROUP_MEETING:{f['member_id']}:{f['learning_cycle_id']}",
        f"GROUP_MEETING:{f['other_member_id']}:{f['learning_cycle_id']}",
    }
    assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"] == before
    for _ in range(9):
        repeated = dry_run_study_meeting_settlement(
            actor_user_id=_admin_id(), session_id=session["id"]
        )
        assert repeated == preview
        assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"] == before


def test_study_meeting_batch_freezes_preview_without_ledger_post() -> None:
    f = _seed_group_leader_fixture()
    _use_credit_plan(f)
    session = create(f)
    _submit_without_evidence(session)
    actor = _admin_id()
    ledger_before = fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]
    batches_before = fetch_one("SELECT COUNT(*) AS n FROM learning_credit_settlement_batches")["n"]

    with pytest.raises(LearningCreditError, match="批次DRY-RUN写入尚未开启"):
        dry_run_study_meeting_batch(actor_user_id=actor, session_id=session["id"])
    with pytest.MonkeyPatch.context() as patch_env:
        patch_env.setenv("LEARNING_CREDIT_BATCH_DRY_RUN_ENABLED", "true")
        patch_env.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")
        first = dry_run_study_meeting_batch(actor_user_id=actor, session_id=session["id"])
        repeated = dry_run_study_meeting_batch(actor_user_id=actor, session_id=session["id"])
        assert first["status"] == "DRY_RUN"
        assert first["proposed_entry_count"] == 2
        assert first["blocked_count"] == 0
        assert first["idempotent"] is False
        assert repeated["idempotent"] is True
        assert repeated["id"] == first["id"]
        assert fetch_one(
            "SELECT COUNT(*) AS n FROM learning_credit_settlement_batch_items WHERE batch_id=?",
            (first["id"],),
        )["n"] == 2
        with transaction() as connection:
            execute(
                connection,
                "UPDATE learning_credit_rules SET points=5 WHERE rule_key='GROUP_MEETING_ATTENDANCE' "
                "AND rule_version_id=(SELECT credit_rule_version_id FROM class_learning_bindings "
                "WHERE class_org_unit_id=? ORDER BY id DESC LIMIT 1)",
                (f["class_id"],),
            )
        with pytest.raises(LearningCreditError, match="冻结规则已变化"):
            dry_run_study_meeting_batch(actor_user_id=actor, session_id=session["id"])

    assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_settlement_batches")["n"] == batches_before + 1
    assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"] == ledger_before


def test_study_meeting_batch_requires_separate_approval_gate_and_permission() -> None:
    f = _seed_group_leader_fixture()
    _use_credit_plan(f)
    session = create(f)
    _submit_without_evidence(session)
    actor = _admin_id()
    before = fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]
    with pytest.MonkeyPatch.context() as patch_env:
        patch_env.setenv("LEARNING_CREDIT_BATCH_DRY_RUN_ENABLED", "true")
        batch = dry_run_study_meeting_batch(actor_user_id=actor, session_id=session["id"])
        submitted = submit_study_meeting_batch_for_approval(actor_user_id=actor, batch_id=batch["id"])
        assert submitted["status"] == "PENDING_APPROVAL"
        with pytest.raises(LearningCreditError, match="批次审批尚未开启"):
            approve_study_meeting_batch(actor_user_id=actor, batch_id=batch["id"])
        patch_env.setenv("LEARNING_CREDIT_BATCH_APPROVAL_ENABLED", "true")
        with pytest.raises(PermissionError, match="无权审批"):
            approve_study_meeting_batch(actor_user_id=actor, batch_id=batch["id"])
        with patch(
            "app.services.learning_credits.user_context",
            return_value={"permissions": ["plans:credit_settlement_approve"]},
        ):
            approved = approve_study_meeting_batch(actor_user_id=actor, batch_id=batch["id"])
        assert approved["status"] == "APPROVED"
        assert fetch_one(
            "SELECT COUNT(*) AS n FROM learning_credit_settlement_batch_items "
            "WHERE batch_id=? AND status='APPROVED'",
            (batch["id"],),
        )["n"] == 2
    assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"] == before


def _approved_study_batch() -> tuple[int, int]:
    f = _seed_group_leader_fixture()
    _use_credit_plan(f)
    session = create(f)
    _submit_without_evidence(session)
    actor = _admin_id()
    with pytest.MonkeyPatch.context() as patch_env:
        patch_env.setenv("LEARNING_CREDIT_BATCH_DRY_RUN_ENABLED", "true")
        patch_env.setenv("LEARNING_CREDIT_BATCH_APPROVAL_ENABLED", "true")
        batch = dry_run_study_meeting_batch(actor_user_id=actor, session_id=session["id"])
        submit_study_meeting_batch_for_approval(actor_user_id=actor, batch_id=batch["id"])
        with patch(
            "app.services.learning_credits.user_context",
            return_value={"permissions": ["plans:credit_settlement_approve"]},
        ):
            approve_study_meeting_batch(actor_user_id=actor, batch_id=batch["id"])
    return actor, batch["id"]


def test_study_meeting_batch_post_reconciles_and_replays_once() -> None:
    actor, batch_id = _approved_study_batch()
    before = fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]
    permissions = ["plans:credit_settlement_manage", "plans:credit_settlement_post"]
    with pytest.raises(LearningCreditError, match="批次正式入账尚未开启"):
        post_study_meeting_batch(actor_user_id=actor, batch_id=batch_id)
    with pytest.MonkeyPatch.context() as patch_env:
        patch_env.setenv("LEARNING_CREDIT_BATCH_POST_ENABLED", "true")
        patch_env.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
        with patch("app.services.learning_credits.user_context", return_value={"permissions": permissions}):
            posted = post_study_meeting_batch(actor_user_id=actor, batch_id=batch_id)
            repeated = post_study_meeting_batch(actor_user_id=actor, batch_id=batch_id)
    assert posted["status"] == "POSTED"
    assert posted["posted_entry_count"] == 2
    assert posted["posted_points"] == "8.00"
    assert repeated["idempotent"] is True
    assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"] == before + 2


def test_study_meeting_batch_resumes_only_failed_item() -> None:
    actor, batch_id = _approved_study_batch()
    before = fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]
    from app.services import learning_credits as credit_service

    original_post = credit_service.post_credit_entry
    attempts = 0

    def fail_second_once(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("isolated-test-failure")
        return original_post(**kwargs)

    permissions = ["plans:credit_settlement_manage", "plans:credit_settlement_post"]
    with pytest.MonkeyPatch.context() as patch_env:
        patch_env.setenv("LEARNING_CREDIT_BATCH_POST_ENABLED", "true")
        patch_env.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
        with patch("app.services.learning_credits.user_context", return_value={"permissions": permissions}):
            with patch(
                "app.services.credit_settlement_batches.credits.post_credit_entry",
                side_effect=fail_second_once,
            ):
                partial = post_study_meeting_batch(actor_user_id=actor, batch_id=batch_id)
            assert partial["status"] == "PARTIAL_FAILED"
            assert partial["posted_entry_count"] == 1
            with pytest.raises(LearningCreditError, match="明确请求续跑"):
                post_study_meeting_batch(actor_user_id=actor, batch_id=batch_id)
            completed = post_study_meeting_batch(
                actor_user_id=actor, batch_id=batch_id, resume_partial_failure=True
            )
    assert completed["status"] == "POSTED"
    assert completed["posted_entry_count"] == 2
    assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"] == before + 2


def test_study_meeting_batch_post_rejects_changed_approved_item() -> None:
    actor, batch_id = _approved_study_batch()
    before = fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]
    with transaction() as connection:
        execute(
            connection,
            "UPDATE learning_credit_settlement_batch_items SET source_id='changed' "
            "WHERE id=(SELECT MIN(id) FROM learning_credit_settlement_batch_items WHERE batch_id=?)",
            (batch_id,),
        )
    permissions = ["plans:credit_settlement_manage", "plans:credit_settlement_post"]
    with pytest.MonkeyPatch.context() as patch_env:
        patch_env.setenv("LEARNING_CREDIT_BATCH_POST_ENABLED", "true")
        patch_env.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
        with patch("app.services.learning_credits.user_context", return_value={"permissions": permissions}):
            with pytest.raises(LearningCreditError, match="审批指纹不匹配"):
                post_study_meeting_batch(actor_user_id=actor, batch_id=batch_id)
    assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"] == before


def test_dry_run_does_not_fallback_to_plans_read_permission() -> None:
    with patch(
        "app.services.learning_credits.user_context",
        return_value={"permissions": ["plans:read"]},
    ):
        with pytest.raises(PermissionError, match="无权预览学分结算"):
            dry_run_study_meeting_settlement(actor_user_id=_admin_id(), session_id=1)


def test_normal_api_submit_path_stays_dry_run_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = _seed_group_leader_fixture()
    key = "Y1-ACCOUNTING-ANALYSIS-TASK"
    _use_credit_plan(f, key)
    monkeypatch.setenv("WECHAT_LOCAL_TEST_MODE", "true")
    monkeypatch.setenv("WECHAT_MEMBER_BINDING_ENABLED", "true")

    with patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={
            "appid": f"credit-test-{f['suffix']}",
            "openid": f"credit-openid-{f['suffix']}",
        },
    ):
        binding = verify_member_binding(code="dev-code", name="V1.2组长", phone=f["phone"])
    member_headers = {"Authorization": "Bearer " + binding["access_token"]}
    admin = fetch_one("SELECT id, token_version FROM app_users WHERE username='admin'")
    admin_headers = {
        "Authorization": "Bearer " + create_token(
            admin["id"], admin["token_version"], "access", timedelta(minutes=5)
        )
    }
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/study-meetings",
            headers=member_headers,
            json={
                "group_org_unit_id": f["group_id"],
                "member_ids": [f["member_id"]],
                "cross_group_member_ids": [f["other_member_id"]],
                "has_course": True,
                "course_keys": [key],
            },
        )
        assert created.status_code == 200, created.text
        session_id = created.json()["data"]["id"]
        uploaded = client.post(
            f"/api/v1/study-meetings/{session_id}/evidence",
            headers=member_headers,
            files={"photo": ("study.jpg", photo(), "image/jpeg")},
        )
        assert uploaded.status_code == 200, uploaded.text
        submitted = client.post(
            f"/api/v1/study-meetings/{session_id}/submit",
            headers=member_headers,
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["data"]["status"] == "SUBMITTED"

    with transaction() as connection:
        execute(
            connection,
            "UPDATE learning_plan_credit_rule_versions SET status='PUBLISHED' "
            "WHERE plan_key=(SELECT p.plan_key FROM learning_plan_versions p "
            "JOIN class_learning_bindings b ON b.plan_version_id=p.id "
            "WHERE b.class_org_unit_id=?)",
            (f["class_id"],),
        )
    before = fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]
    with TestClient(app) as client:
        preview = client.post(
            f"/api/v1/learning-credits/dry-run/study-meetings/{session_id}",
            headers=admin_headers,
        )
    assert preview.status_code == 200, preview.text
    data = preview.json()["data"]
    assert data["mode"] == "DRY_RUN"
    assert data["persisted"] is False
    assert data["totals"]["proposed_points"] == 88
    assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"] == before


def test_reverse_rejects_unscoped_credit_entry() -> None:
    actor = _admin_id()
    version = fetch_one(
        "SELECT id, version_label FROM learning_credit_rule_versions "
        "WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1'"
    )
    member_id = int(fetch_one("SELECT id FROM members ORDER BY id LIMIT 1")["id"])
    key = f"UNSCOPED:{uuid4().hex}"
    item = {
        "member_id": member_id,
        "credit_category": "STANDARD_LEARNING",
        "credit_type": "MANUAL_ADJUSTMENT",
        "points": 40,
        "source_type": "MANUAL_ADJUSTMENT",
        "source_id": key,
        "rule_key": "MANUAL_ADJUSTMENT",
        "rule_version": version["version_label"],
        "rule_version_id": version["id"],
        "rule_snapshot": {"reason": "组织范围测试"},
        "occurred_at": "2026-09-06",
        "idempotency_key": key,
    }
    with pytest.MonkeyPatch.context() as patch_env:
        patch_env.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
        original = post_credit_entry(actor_user_id=actor, item=item)
        with pytest.raises(PermissionError, match="无组织范围"):
            reverse_credit_entry(actor_user_id=actor, entry_id=original["id"], reason="范围测试")
    assert fetch_one("SELECT status FROM learning_credit_entries WHERE id=?", (original["id"],))["status"] == "POSTED"


def test_course_completion_fact_and_plan_rule_version_gate() -> None:
    f = _seed_group_leader_fixture()
    key = "Y1-ACCOUNTING-ANALYSIS-TASK"
    _use_credit_plan(f, key)
    session = create(f, [key])
    _submit_without_evidence(session)

    pending_plan = dry_run_study_meeting_settlement(
        actor_user_id=_admin_id(), session_id=session["id"]
    )
    course = next(item for item in pending_plan["entries"] if item["entry_kind"] == "COURSE_COMPLETION")
    assert course["points"] == 40
    assert course["status"] == "BLOCKED"
    assert any("课程积分版本未发布" in reason for reason in course["reasons"])
    completion_count = fetch_one(
        "SELECT COUNT(*) AS n FROM study_meeting_course_completions "
        "WHERE study_meeting_course_id=(SELECT id FROM study_meeting_courses WHERE study_meeting_session_id=?)",
        (session["id"],),
    )["n"]
    assert completion_count == 2

    with transaction() as connection:
        now = "2026-09-06T00:00:00+00:00"
        execute(
            connection,
            "UPDATE learning_plan_credit_rule_versions SET status='PUBLISHED' "
            "WHERE plan_key=(SELECT p.plan_key FROM learning_plan_versions p "
            "JOIN class_learning_bindings b ON b.plan_version_id=p.id WHERE b.class_org_unit_id=?)",
            (f["class_id"],),
        )
    ready = dry_run_study_meeting_settlement(
        actor_user_id=_admin_id(), session_id=session["id"]
    )
    course_rows = [item for item in ready["entries"] if item["entry_kind"] == "COURSE_COMPLETION"]
    assert len(course_rows) == 2
    assert all(item["status"] == "READY" and item["points"] == 40 for item in course_rows)

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
        posted = settle_study_meeting(actor_user_id=_admin_id(), session_id=session["id"])
        repeated = settle_study_meeting(actor_user_id=_admin_id(), session_id=session["id"])
    assert posted["persisted"] is True
    assert posted["total_points"] == 88
    assert repeated["idempotent"] is True
    assert fetch_one(
        "SELECT COUNT(*) AS n FROM learning_credit_entries "
        "WHERE source_type IN ('STUDY_MEETING_ATTENDANCE', 'STUDY_MEETING_COURSE') "
        "AND source_id IN (?, ?)",
        (str(session["id"]), str(fetch_one("SELECT id FROM study_meeting_courses WHERE study_meeting_session_id=?", (session["id"],))["id"])),
    )["n"] == 4


def test_completion_confirmation_can_block_only_the_course_credit() -> None:
    f = _seed_group_leader_fixture()
    _use_credit_plan(f, "Y1-HAPPINESS-ASSESSMENT")
    session = create(f, ["Y1-HAPPINESS-ASSESSMENT"])
    _submit_without_evidence(session)
    confirm_study_meeting_course_completion(
        actor_user_id=_admin_id(), session_id=session["id"],
        course_key="Y1-HAPPINESS-ASSESSMENT", member_id=f["member_id"],
        completion_status="NOT_COMPLETED", note="对账抽查",
    )
    preview = dry_run_study_meeting_settlement(
        actor_user_id=_admin_id(), session_id=session["id"]
    )
    group = [item for item in preview["entries"] if item["entry_kind"] == "GROUP_MEETING_ATTENDANCE"]
    course = [item for item in preview["entries"] if item["entry_kind"] == "COURSE_COMPLETION"]
    assert all(item["status"] == "READY" for item in group)
    assert course[0]["status"] == "BLOCKED"
    assert any("实际完成尚未确认" in reason for reason in course[0]["reasons"])


def test_settlement_posts_ready_group_entries_when_course_is_blocked() -> None:
    f = _seed_group_leader_fixture()
    _use_credit_plan(f, "Y1-ACCOUNTING-ANALYSIS-TASK")
    session = create(f, ["Y1-ACCOUNTING-ANALYSIS-TASK"])
    _submit_without_evidence(session)
    confirm_study_meeting_course_completion(
        actor_user_id=_admin_id(), session_id=session["id"],
        course_key="Y1-ACCOUNTING-ANALYSIS-TASK", member_id=f["member_id"],
        completion_status="NOT_COMPLETED", note="仅阻塞课程分",
    )

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
        settled = settle_study_meeting(actor_user_id=_admin_id(), session_id=session["id"])

    assert settled["total_points"] == 8
    assert len(settled["entries"]) == 2
    assert all(item["credit_type"] == "GROUP_MEETING_ATTENDANCE" for item in settled["entries"])


def test_ledger_reversal_is_append_only_and_idempotent() -> None:
    f = _seed_group_leader_fixture()
    actor = _admin_id()
    key = f"MANUAL:{uuid4().hex}"
    version = fetch_one(
        "SELECT id, version_label FROM learning_credit_rule_versions "
        "WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1'"
    )
    assert version
    item = {
        "member_id": f["member_id"],
        "credit_category": "STANDARD_LEARNING",
        "credit_type": "MANUAL_ADJUSTMENT",
        "points": 40,
        "source_type": "MANUAL_ADJUSTMENT",
        "source_id": key,
        "class_org_unit_id": f["class_id"],
        "rule_key": "MANUAL_ADJUSTMENT",
        "rule_version": version["version_label"],
        "rule_version_id": version["id"],
        "rule_snapshot": {"reason": "测试补分"},
        "occurred_at": "2026-09-06",
        "idempotency_key": key,
    }
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
        original = post_credit_entry(actor_user_id=actor, item=item)
        assert member_credit_summary(actor_user_id=actor, member_id=f["member_id"])["total_points"] == 40
        original_row = fetch_one(
            "SELECT * FROM learning_credit_entries WHERE id=?", (original["id"],)
        )
        reversal = reverse_credit_entry(actor_user_id=actor, entry_id=original["id"], reason="核对后冲销")
        repeated = reverse_credit_entry(actor_user_id=actor, entry_id=original["id"], reason="核对后冲销")
    assert reversal["points"] == -40
    assert repeated["id"] == reversal["id"]
    assert fetch_one(
        "SELECT * FROM learning_credit_entries WHERE id=?", (original["id"],)
    ) == original_row
    assert fetch_one("SELECT status FROM learning_credit_entries WHERE id=?", (original["id"],))["status"] == "POSTED"
    assert fetch_one(
        "SELECT reversal_of_entry_id, status FROM learning_credit_entries WHERE id=?",
        (reversal["id"],),
    ) == {"reversal_of_entry_id": original["id"], "status": "POSTED"}
    assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries WHERE idempotency_key=?", (f"REVERSAL:{original['id']}",))["n"] == 1
    assert member_credit_summary(actor_user_id=actor, member_id=f["member_id"])["total_points"] == 0


@pytest.mark.parametrize(
    "changed",
    [
        {"points": 41},
        {"source_id": "different-source"},
        {"rule_snapshot": {"reason": "different-rule"}},
        {"occurred_at": "2026-09-07"},
    ],
)
def test_post_rejects_same_idempotency_key_for_different_fact(changed: dict) -> None:
    f = _seed_group_leader_fixture()
    actor = _admin_id()
    version = fetch_one(
        "SELECT id, version_label FROM learning_credit_rule_versions "
        "WHERE rule_set_key='STANDARD_3Y_2026' AND version_label='2026.1'"
    )
    key = f"MANUAL:{uuid4().hex}"
    item = {
        "member_id": f["member_id"],
        "credit_category": "STANDARD_LEARNING",
        "credit_type": "MANUAL_ADJUSTMENT",
        "points": 40,
        "source_type": "MANUAL_ADJUSTMENT",
        "source_id": key,
        "class_org_unit_id": f["class_id"],
        "rule_key": "MANUAL_ADJUSTMENT",
        "rule_version": version["version_label"],
        "rule_version_id": version["id"],
        "rule_snapshot": {"reason": "original-rule"},
        "occurred_at": "2026-09-06",
        "idempotency_key": key,
    }
    with pytest.MonkeyPatch.context() as patch_env:
        patch_env.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
        original = post_credit_entry(actor_user_id=actor, item=item)
        assert post_credit_entry(actor_user_id=actor, item=item)["id"] == original["id"]
        with pytest.raises(LearningCreditError, match="幂等键已关联不同学分事实"):
            post_credit_entry(actor_user_id=actor, item={**item, **changed})
    assert fetch_one(
        "SELECT COUNT(*) AS n FROM learning_credit_entries WHERE idempotency_key=?",
        (key,),
    )["n"] == 1
