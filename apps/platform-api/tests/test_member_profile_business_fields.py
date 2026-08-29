from __future__ import annotations

from uuid import uuid4

from app.db import execute, fetch_one, transaction
from app.services.members import create_member, get_member_edit_profile, update_member


def test_member_edit_profile_persists_business_fields() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin is not None
    suffix = uuid4().hex[:8]
    center_id = f"profile-fields-center-{suffix}"
    now = "2026-08-27T00:00:00+00:00"
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, '档案字段测试分中心', 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
            (center_id, f"PROFILE_FIELDS_{suffix}", now, now),
        )
    member_id = create_member(
        admin["id"],
        member_code=f"PROFILE-FIELDS-{suffix}",
        name="学员档案字段测试",
        org_unit_id=center_id,
        development_org_unit_id=None,
        phone=None,
        political_status="党员",
        social_role="政协委员",
        email="profile-fields@example.com",
        invoice_type="NORMAL",
        invoice_title="测试企业",
        invoice_tax_id="91320000PROFILE",
        goal_years="3年",
        revenue_growth_target="2倍",
        profit_growth_target="1.5倍",
    )

    profile = get_member_edit_profile(member_id, admin["id"])
    assert profile["political_status"] == "党员"
    assert profile["social_role"] == "政协委员"
    assert profile["email"] == "profile-fields@example.com"
    assert profile["invoice_type"] == "NORMAL"
    assert profile["invoice_title"] == "测试企业"
    assert profile["invoice_tax_id"] == "91320000PROFILE"
    assert profile["goal_years"] == "3年"
    assert profile["revenue_growth_target"] == "2倍"
    assert profile["profit_growth_target"] == "1.5倍"

    update_member(
        admin["id"],
        member_id,
        {
            "political_status": "党员",
            "social_role": "党组织负责人",
            "email": "updated-profile-fields@example.com",
            "invoice_type": "SPECIAL",
            "invoice_title": "更新后企业",
            "invoice_tax_id": "91320000UPDATED",
            "goal_years": "5年",
            "revenue_growth_target": "3倍",
            "profit_growth_target": "2倍",
        },
    )
    row = fetch_one(
        "SELECT political_status, social_role, email, invoice_type, invoice_title, invoice_tax_id, "
        "goal_years, revenue_growth_target, profit_growth_target FROM members WHERE id=?",
        (member_id,),
    )
    assert row == {
        "political_status": "党员",
        "social_role": "党组织负责人",
        "email": "updated-profile-fields@example.com",
        "invoice_type": "SPECIAL",
        "invoice_title": "更新后企业",
        "invoice_tax_id": "91320000UPDATED",
        "goal_years": "5年",
        "revenue_growth_target": "3倍",
        "profit_growth_target": "2倍",
    }

    update_member(admin["id"], member_id, {"political_status": "群众"})
    cleared = fetch_one("SELECT political_status, social_role FROM members WHERE id=?", (member_id,))
    assert cleared == {"political_status": "群众", "social_role": None}
