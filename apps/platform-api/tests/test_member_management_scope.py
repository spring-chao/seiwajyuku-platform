from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.db import execute, fetch_one, transaction
from app.services.iam import create_user
from app.services.members import list_member_org_catalog, list_members_page
from app.services.organization_policy import is_valid_member_primary_org


def _admin_id() -> int:
    row = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert row is not None
    return int(row["id"])


def test_member_catalog_is_master_based_and_keeps_empty_wuxi_tree() -> None:
    catalog = list_member_org_catalog(_admin_id())
    assert {item["unit_code"] for item in catalog["shukus"]} >= {
        "SZ_ROOT",
        "CZ_ROOT",
        "WX_ROOT",
    }
    assert {item["unit_code"] for item in catalog["management_units"]} >= {
        "WX_GUIDANCE_1",
        "WX_GUIDANCE_2",
    }
    assert "WX_JING_JIN" in {item["unit_code"] for item in catalog["classes"]}


def test_member_list_is_server_paginated_with_independent_status_and_keyword() -> None:
    suffix = uuid4().hex[:8]
    center_id = f"member-page-center-{suffix}"
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, 'REGIONAL_CENTER', 'org-changzhou', 1, ?, ?)",
            (center_id, f"MEMBER_PAGE_{suffix}", "分页测试分中心", now, now),
        )
        for index in range(55):
            execute(
                connection,
                "INSERT INTO members(member_code, name, org_unit_id, status, phone_last4, phone_masked, created_at, updated_at) "
                "VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?)",
                (
                    f"MEMBER_PAGE_{suffix}_{index}",
                    f"分页学长{index:02d}",
                    center_id,
                    f"{index:04d}",
                    f"****{index:04d}",
                    now,
                    now,
                ),
            )
        execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'INACTIVE', ?, ?)",
            (f"MEMBER_PAGE_{suffix}_inactive", "分页历史学长", center_id, now, now),
        )

    first = list_members_page(_admin_id(), management_org_unit_id=center_id)
    assert first["pagination"] == {
        "page": 1,
        "page_size": 50,
        "total": 55,
        "total_pages": 2,
    }
    assert first["summary"]["active_count"] == 55
    assert len(first["items"]) == 50
    second = list_members_page(
        _admin_id(), management_org_unit_id=center_id, page=2, page_size=50
    )
    assert len(second["items"]) == 5
    assert all(item["status"] == "ACTIVE" for item in second["items"])
    keyword = list_members_page(
        _admin_id(), management_org_unit_id=center_id, keyword="历史"
    )
    assert keyword["pagination"]["total"] == 0
    inactive = list_members_page(
        _admin_id(),
        management_org_unit_id=center_id,
        status="INACTIVE",
        keyword="历史",
    )
    assert inactive["pagination"]["total"] == 1
    assert inactive["items"][0]["name"] == "分页历史学长"


def test_member_org_filter_rejects_out_of_scope_ids() -> None:
    user_id = create_user(
        _admin_id(),
        username=f"member-page-scope-{uuid4().hex[:8]}",
        display_name="分页范围测试",
        password="member-page-password",
        roles=["ops_center_operations"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": "org-suzhou"}],
    )
    with pytest.raises(PermissionError):
        list_members_page(user_id, shuku_org_unit_id="org-wuxi")


def test_wuxi_primary_org_policy_is_explicit() -> None:
    assert is_valid_member_primary_org(
        org_unit_id="org-wuxi-guidance-1",
        unit_type="OPERATING_UNIT",
        parent_id="org-wuxi",
    )
    assert is_valid_member_primary_org(
        org_unit_id="org-wuxi-guidance-2",
        unit_type="OPERATING_UNIT",
        parent_id="org-wuxi",
    )
    assert not is_valid_member_primary_org(
        org_unit_id="org-wuxi-jing-jin",
        unit_type="SPECIAL_COHORT",
        parent_id="org-wuxi",
    )
    assert not is_valid_member_primary_org(
        org_unit_id="other-operating-unit",
        unit_type="OPERATING_UNIT",
        parent_id="org-wuxi",
    )
