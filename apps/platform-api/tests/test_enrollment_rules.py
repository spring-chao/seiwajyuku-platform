import json

import pytest

from app.services.enrollment import _public_shuku_profile
from app.services.enrollment_rules import joining_rules_document
from app.services.organization_policy import SUZHOU_ROOT_ORG_UNIT_ID


@pytest.mark.parametrize("scope", [None, "", "org-wuxi", "org-changzhou", "synthetic-other"])
def test_suzhou_policy_never_uses_display_name_or_other_shuku(scope):
    assert joining_rules_document(scope, {"display_name": "苏州塾", "fee_amount": "4800"}) is None


def test_suzhou_document_preserves_learning_and_review_rules():
    doc = joining_rules_document("org-suzhou", {"fee_amount": "4800", "fee_unit": "元/人/年"})
    assert doc["scope_org_unit_id"] == "org-suzhou"
    assert [section["id"] for section in doc["sections"]] == ["eligibility", "process", "payment", "study", "refund"]
    text = json.dumps(doc, ensure_ascii=False)
    for fact in ("企业高管", "股东、董事、总经理", "发展建设委组长", "一方不同意", "两方均不同意", "30天", "3次", "4篇", "120字", "400分", "不予退还"):
        assert fact in text
    points = next(section for section in doc["sections"] if section["id"] == "study")["points"]
    assert [row["points"] for row in points] == [100, 50, 50, 200, 200]
    assert sum(row["points"] for row in points) == 600
    assert "4800 元/人/年" in text


def test_fee_is_from_selected_profile_and_no_account_or_phone_is_embedded():
    doc = joining_rules_document("org-suzhou", {"fee_amount": "5000", "fee_unit": "元/人/年",
        "payment": {"bank_account": "SYNTHETIC-ACCOUNT"}, "contacts": [{"phone": "SYNTHETIC-CONTACT"}]})
    text = json.dumps(doc, ensure_ascii=False)
    assert "5000 元/人/年" in text
    assert "4800" not in text
    assert "SYNTHETIC-ACCOUNT" not in text and "SYNTHETIC-CONTACT" not in text


def test_missing_fee_is_not_invented_and_documents_are_independent():
    first = joining_rules_document("org-suzhou", {})
    assert "请向工作人员确认" in first["summary"][2]
    assert "4800" not in json.dumps(first, ensure_ascii=False)
    first["sections"][0]["paragraphs"].clear()
    assert joining_rules_document("org-suzhou", {})["sections"][0]["paragraphs"]


@pytest.mark.parametrize("scope", [SUZHOU_ROOT_ORG_UNIT_ID, "org-wuxi"])
@pytest.mark.parametrize("legacy_profile_contact", [False, True])
def test_public_contact_spelling_is_scoped_and_preserves_configured_data(
    monkeypatch, scope, legacy_profile_contact
):
    profile = {
        "display_name": "苏州塾", "joining_notice": "测试加入说明",
        "contact_name": "张玲嫒", "contact_phone": "SYNTHETIC-PRIMARY-CONTACT",
    }
    contact_rows = [
        {"contact_name": "张玲嫒", "contact_phone": "SYNTHETIC-PRIMARY-CONTACT", "sort_order": 10},
        {"contact_name": "测试第二联系人", "contact_phone": "SYNTHETIC-SECOND-CONTACT", "sort_order": 20},
    ] if not legacy_profile_contact else []
    def configured_profile(query, params):
        return profile if "enrollment_shuku_profiles " in query else {}

    monkeypatch.setattr("app.services.enrollment.fetch_one", configured_profile)
    monkeypatch.setattr("app.services.enrollment.fetch_all", lambda query, params: contact_rows)

    public_profile = _public_shuku_profile(scope)
    expected_name = "张玲嫣" if scope == SUZHOU_ROOT_ORG_UNIT_ID else "张玲嫒"
    assert public_profile["contacts"][0]["name"] == expected_name
    assert public_profile["contact"]["contact_name"] == expected_name
    assert public_profile["contacts"][0]["phone"] == "SYNTHETIC-PRIMARY-CONTACT"
    assert public_profile["contact"]["contact_phone"] == "SYNTHETIC-PRIMARY-CONTACT"
    assert profile["contact_name"] == "张玲嫒"
    if not legacy_profile_contact:
        assert contact_rows[0]["contact_name"] == "张玲嫒"
        assert public_profile["contacts"][1] == {
            "name": "测试第二联系人", "phone": "SYNTHETIC-SECOND-CONTACT", "sort_order": 20
        }
