import json

import pytest

from app.services.enrollment_rules import joining_rules_document


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
