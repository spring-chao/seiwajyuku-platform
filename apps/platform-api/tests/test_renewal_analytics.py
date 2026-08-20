from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.api import renewals as renewals_api
from app.db import execute, fetch_one, transaction
from app.services.iam import create_user
from app.services.renewal_analytics import (
    HISTORICAL_AUTO_RECONCILIATION,
    IMPORT_SNAPSHOT,
    LIVE_STATUS_TRANSITION,
    UNKNOWN,
    get_annual_renewal_analytics,
)


def _analytics_fixture() -> tuple[int, str, str, int]:
    suffix = uuid4().hex[:8]
    center_id = f"analytics-center-{suffix}"
    other_center_id = f"analytics-other-{suffix}"
    now = datetime.now(UTC).isoformat()
    admin_id = int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])

    with transaction() as connection:
        for org_id, code, name in (
            (center_id, f"ANALYTICS_{suffix}", "续费分析测试分中心"),
            (other_center_id, f"ANALYTICS_OTHER_{suffix}", "续费分析其他分中心"),
        ):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                (org_id, code, name, now, now),
            )
        import_batch_id = execute(
            connection,
            "INSERT INTO renewal_import_batches(source_name, source_sha256, status, preview_json, created_by, created_at) "
            "VALUES ('analytics-import.xlsx', ?, 'APPLIED', '{}', ?, ?)",
            (f"{suffix:0<64}", admin_id, now),
        ).lastrowid

        def add_cycle(
            name: str,
            due_month: int,
            status: str,
            org_id: str = center_id,
            completed_at: str | None = None,
            history: tuple[str | None, str, str | None, str] | None = None,
            source_batch_id: int | None = None,
        ) -> int:
            member_id = execute(
                connection,
                "INSERT INTO members(member_code, name, org_unit_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'ACTIVE', ?, ?)",
                (f"ANALYTICS-MEMBER-{suffix}-{name}", name, org_id, now, now),
            ).lastrowid
            cycle_id = execute(
                connection,
                "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, completed_at, source_batch_id, created_at, updated_at) "
                "VALUES (?, 2099, ?, ?, ?, ?, ?, ?, ?)",
                (member_id, org_id, due_month, status, completed_at, source_batch_id, now, now),
            ).lastrowid
            if history:
                from_status, to_status, reason, created_at = history
                execute(
                    connection,
                    "INSERT INTO renewal_status_history(renewal_cycle_id, from_status, to_status, reason, changed_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (cycle_id, from_status, to_status, reason, admin_id, created_at),
                )
            return int(cycle_id)

        for stage_name, completed in (
            ("准备阶段可信", "2099-07-01T09:00:00+00:00"),
            ("观3可信", "2099-08-01T09:00:00+00:00"),
            ("续2可信", "2099-09-01T09:00:00+00:00"),
            ("追1可信", "2099-10-01T09:00:00+00:00"),
            ("到期月可信", "2099-11-01T09:00:00+00:00"),
            ("到期后可信", "2099-12-01T09:00:00+00:00"),
        ):
            add_cycle(
                stage_name,
                11,
                "RENEWED",
                completed_at=completed,
                history=("IN_COMMUNICATION", "RENEWED", None, completed),
            )
        add_cycle(
            "历史自动补录",
            11,
            "RENEWED",
            completed_at="2099-08-05T09:00:00+00:00",
            history=(
                "IN_COMMUNICATION",
                "RENEWED",
                "学员管理维护历史续费月份，已有周期自动标记为已续费",
                "2099-08-05T09:00:00+00:00",
            ),
        )
        add_cycle(
            "正式导入快照",
            11,
            "RENEWED",
            completed_at="2099-08-06T09:00:00+00:00",
            history=(None, "RENEWED", "续费名单正式导入（批次 #1）", "2099-08-06T09:00:00+00:00"),
            source_batch_id=int(import_batch_id),
        )
        add_cycle("未知完成证据", 11, "RENEWED", completed_at="2099-08-07T09:00:00+00:00")
        add_cycle("明确不续", 11, "NOT_RENEWING")
        add_cycle("已退出", 11, "EXITED")
        add_cycle("延期沟通", 11, "DEFERRED")
        add_cycle("推进中", 11, "IN_COMMUNICATION")
        add_cycle("其他组织", 11, "RENEWED", org_id=other_center_id)
    scoped_user = create_user(
        admin_id,
        username=f"analytics-user-{suffix}",
        display_name="续费分析分中心账号",
        password="analytics-test-password",
        roles=["ops_center_operations"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": center_id}],
    )
    return admin_id, center_id, other_center_id, scoped_user


def test_annual_analytics_classifies_evidence_and_timing_without_fabrication() -> None:
    admin_id, center_id, other_center_id, _ = _analytics_fixture()
    result = get_annual_renewal_analytics(admin_id, 2099, org_unit_id=center_id)

    assert result["total_cycles"] == 13
    assert result["renewed_count"] == 9
    assert result["not_renewing_count"] == 1
    assert result["exited_count"] == 1
    assert result["deferred_count"] == 1
    assert result["open_count"] == 2
    assert result["reliable_completion_count"] == 6
    assert result["unreliable_completion_count"] == 3
    assert result["evidence_counts"] == {
        LIVE_STATUS_TRANSITION: 6,
        IMPORT_SNAPSHOT: 1,
        HISTORICAL_AUTO_RECONCILIATION: 1,
        UNKNOWN: 1,
    }
    assert result["stage_counts"] == {
        "PREPARE": 1,
        "OBSERVE_3": 1,
        "RENEW_2": 1,
        "FOLLOW_1": 1,
        "DUE_NOW": 1,
        "RECOVERY": 1,
    }
    assert result["before_due_count"] == 4
    assert result["due_month_count"] == 1
    assert result["after_due_count"] == 1
    assert result["before_due_rate_among_reliable_renewals"] == 0.6667
    other_result = get_annual_renewal_analytics(admin_id, 2099, org_unit_id=other_center_id)
    other_org = next(row for row in other_result["organizations"] if row["org_unit_id"] == other_center_id)
    assert other_org["reliable_completion_count"] == 0
    assert other_org["before_due_rate_among_reliable_renewals"] is None
    assert any(row["org_unit_id"] == center_id for row in result["organizations"])
    serialized = json.dumps(result, ensure_ascii=False)
    assert "手机号" not in serialized
    assert "联系电话" not in serialized
    assert "资金" not in serialized


def test_annual_analytics_org_scope_and_api_permission() -> None:
    admin_id, center_id, other_center_id, scoped_user = _analytics_fixture()
    scoped = get_annual_renewal_analytics(scoped_user, 2099)
    assert all(row["org_unit_id"] == center_id for row in scoped["organizations"])
    with pytest.raises(PermissionError):
        get_annual_renewal_analytics(scoped_user, 2099, org_unit_id=other_center_id)
    with pytest.raises(renewals_api.HTTPException) as exc:
        renewals_api.annual_analytics(
            year=2099,
            org_unit_id=other_center_id,
            user={"id": scoped_user},
        )
    assert exc.value.status_code == 403
