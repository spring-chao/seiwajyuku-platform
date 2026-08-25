from __future__ import annotations

import json
import os
import secrets
import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.core.privacy import decrypt_text, phone_hash
from app.core.settings import get_settings
from app.db import execute, fetch_all, fetch_one, transaction
from app.main import app, read_only_request_allowed
from app.services.iam import create_user
from app.services.members import create_member


def _phone() -> str:
    return f"139{uuid4().int % 100_000_000:08d}"


class EnrollmentApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()
        login = cls.client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",
                "password": os.environ["BOOTSTRAP_ADMIN_PASSWORD"],
            },
        )
        if login.status_code != 200:
            raise AssertionError(login.text)
        cls.headers = {
            "Authorization": f"Bearer {login.json()['data']['access_token']}"
        }
        cls.admin_id = fetch_one(
            "SELECT id FROM app_users WHERE username='admin'"
        )["id"]
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            if not execute(
                connection,
                "SELECT id FROM org_units WHERE id='enrollment-test-center'",
            ).fetchone():
                execute(
                    connection,
                    "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
                    "is_active, created_at, updated_at) VALUES "
                    "('enrollment-test-center', 'ENROLLMENT_TEST_CENTER', ?, "
                    "'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                    ("入塾申请测试分中心", now, now),
                )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)

    def setUp(self) -> None:
        with transaction() as connection:
            execute(connection, "DELETE FROM member_enrollment_submission_guards")
            execute(connection, "DELETE FROM member_enrollment_applications")
            execute(connection, "DELETE FROM member_enrollment_links")

    def _create_link(self) -> tuple[int, str]:
        response = self.client.post(
            "/api/v1/enrollment-links",
            headers=self.headers,
            json={"name": "全局入塾申请码"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        return data["id"], data["raw_token"]

    def _submit(self, token: str, phone: str, **overrides):
        payload = {
            "name": "申请测试学长",
            "phone": phone,
            "privacy_consent": True,
            "birthday": "1980-01-02",
            "company_name": "申请测试企业",
            "company_address": "苏州工业园区测试路1号",
            "position": "总经理",
            "referrer": "推荐测试学长",
            "invoice_info": "申请测试企业|91320000TEST2026|苏州工业园区测试路1号",
            "invoice_type": "增值税普通发票",
            "industry": "制造业",
            "company_products": "测试产品",
            "employee_count": 20,
            "books_read": "活法",
            "enrollment_reason_philosophy": "认同敬天爱人的理念",
            "enrollment_reason_change": "提升经营能力",
            "enrollment_reason_other": "修身齐家治企",
            "learning_years_goal": "长期坚持学习",
            "learning_participation_goal": "保持学习打卡并参加活动",
            "business_goal": "提升销售额和利润率",
            "other_goal": "为社会做出贡献",
            "annual_sales": "测试销售额区间",
            "profit_margin": "测试利润率区间",
            "notes": "希望了解学习安排",
        }
        payload.update(overrides)
        return self.client.post(
            f"/api/v1/public/enrollment/{token}", json=payload
        )

    def test_public_form_has_no_organization_and_rejects_org_payload(self) -> None:
        _, token = self._create_link()
        form = self.client.get(f"/api/v1/public/enrollment/{token}")
        self.assertEqual(form.status_code, 200, form.text)
        data = form.json()["data"]
        self.assertFalse(data["collects_organization"])
        self.assertIn("company_address", data["required_fields"])
        self.assertIn("employee_count", data["required_fields"])
        self.assertIn("books_read", data["required_fields"])
        self.assertIn("enrollment_reason_philosophy", data["required_fields"])
        self.assertIn("annual_sales", data["required_fields"])
        self.assertIn("profit_margin", data["optional_fields"])
        rejected = self._submit(
            token, _phone(), org_unit_id="enrollment-test-center"
        )
        self.assertEqual(rejected.status_code, 422, rejected.text)

    def test_public_submit_is_protected_generic_and_does_not_create_member(self) -> None:
        _, token = self._create_link()
        phone = _phone()
        before_members = len(fetch_all("SELECT id FROM members"))
        first = self._submit(token, phone)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(
            first.json()["data"]["message"], "申请已提交，请等待工作人员联系。"
        )
        row = fetch_one(
            "SELECT * FROM member_enrollment_applications WHERE phone_hash=?",
            (phone_hash(phone),),
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["application_status"], "SUBMITTED")
        self.assertEqual(row["payment_status"], "UNCONFIRMED")
        self.assertIsNone(row["org_unit_id"])
        self.assertEqual(row["company_address"], "苏州工业园区测试路1号")
        self.assertEqual(row["invoice_type"], "增值税普通发票")
        self.assertEqual(row["employee_count"], 20)
        self.assertEqual(row["books_read"], "活法")
        self.assertEqual(row["enrollment_reason_philosophy"], "认同敬天爱人的理念")
        self.assertNotEqual(row["phone_ciphertext"], phone)
        self.assertNotIn("测试销售额", row["enterprise_financial_ciphertext"])
        self.assertEqual(len(fetch_all("SELECT id FROM members")), before_members)

        duplicate = self._submit(token, phone, name="再次提交也不泄露")
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json()["data"], first.json()["data"])
        count = fetch_one(
            "SELECT COUNT(*) AS total FROM member_enrollment_applications "
            "WHERE phone_hash=?",
            (phone_hash(phone),),
        )
        self.assertEqual(count["total"], 1)

    def test_review_payment_and_enroll_are_gated_and_idempotent(self) -> None:
        _, token = self._create_link()
        phone = _phone()
        submitted = self._submit(token, phone)
        self.assertEqual(submitted.status_code, 200, submitted.text)
        application_id = fetch_one(
            "SELECT id FROM member_enrollment_applications WHERE phone_hash=?",
            (phone_hash(phone),),
        )["id"]

        blocked = self.client.post(
            f"/api/v1/enrollment-applications/{application_id}/enroll",
            headers=self.headers,
        )
        self.assertEqual(blocked.status_code, 400, blocked.text)
        self.assertIn("审核", blocked.json()["detail"])

        reviewed = self.client.patch(
            f"/api/v1/enrollment-applications/{application_id}/review",
            headers=self.headers,
            json={
                "decision": "APPROVE",
                "org_unit_id": "enrollment-test-center",
                "join_date": "2026-08-24",
                "review_note": "资料已核对",
            },
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertIn("尚未确认收款", reviewed.json()["data"]["missing_gates"])
        self.assertEqual(
            reviewed.json()["data"]["annual_sales"], "测试销售额区间"
        )

        synthetic_amount = f"{uuid4().int % 90 + 10}.00"
        paid = self.client.post(
            f"/api/v1/enrollment-applications/{application_id}/payment-confirmation",
            headers=self.headers,
            json={"payment_status": "PAID", "amount": synthetic_amount},
        )
        self.assertEqual(paid.status_code, 200, paid.text)
        self.assertTrue(paid.json()["data"]["can_enroll"])

        enrolled = self.client.post(
            f"/api/v1/enrollment-applications/{application_id}/enroll",
            headers=self.headers,
        )
        self.assertEqual(enrolled.status_code, 200, enrolled.text)
        member_id = enrolled.json()["data"]["member_id"]
        self.assertFalse(enrolled.json()["data"]["idempotent"])
        repeated = self.client.post(
            f"/api/v1/enrollment-applications/{application_id}/enroll",
            headers=self.headers,
        )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()["data"]["member_id"], member_id)
        self.assertTrue(repeated.json()["data"]["idempotent"])

        member = fetch_one("SELECT * FROM members WHERE id=?", (member_id,))
        self.assertEqual(member["status"], "ACTIVE")
        self.assertEqual(member["org_unit_id"], "enrollment-test-center")
        self.assertEqual(member["phone_hash"], phone_hash(phone))
        financial = json.loads(decrypt_text(member["enterprise_financial_ciphertext"]))
        self.assertEqual(financial["annual_sales"], "测试销售额区间")
        relation = fetch_one(
            "SELECT source_type FROM member_org_relations WHERE member_id=? "
            "AND relation_type='PRIMARY_REGION'",
            (member_id,),
        )
        self.assertEqual(relation["source_type"], "ENROLLMENT_APPLICATION")
        application = fetch_one(
            "SELECT application_status, converted_member_id, active_phone_guard "
            "FROM member_enrollment_applications WHERE id=?",
            (application_id,),
        )
        self.assertEqual(application["application_status"], "ENROLLED")
        self.assertEqual(application["converted_member_id"], member_id)
        self.assertIsNone(application["active_phone_guard"])
        audits = "\n".join(
            row["after_json"] or ""
            for row in fetch_all(
                "SELECT after_json FROM audit_logs WHERE resource_type IN "
                "('member_enrollment_application', 'member') ORDER BY id DESC LIMIT 20"
            )
        )
        self.assertNotIn(phone, audits)
        self.assertNotIn("测试销售额区间", audits)
        self.assertNotIn("测试利润率区间", audits)

    def test_existing_member_is_only_a_private_risk_flag(self) -> None:
        _, token = self._create_link()
        phone = _phone()
        create_member(
            self.admin_id,
            member_code=f"ENROLLMENT-DUP-{uuid4().hex[:8]}",
            name="已有正式档案学长",
            org_unit_id="enrollment-test-center",
            development_org_unit_id=None,
            phone=phone,
        )
        submitted = self._submit(token, phone)
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertNotIn("已有", submitted.text)
        row = fetch_one(
            "SELECT id, duplicate_member_risk FROM member_enrollment_applications "
            "WHERE phone_hash=?",
            (phone_hash(phone),),
        )
        self.assertEqual(row["duplicate_member_risk"], 1)
        detail = self.client.get(
            f"/api/v1/enrollment-applications/{row['id']}", headers=self.headers
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertIn("疑似已有正式学员档案", "；".join(detail.json()["data"]["missing_gates"]))

    def test_scoped_reviewer_cannot_see_unassigned_and_finance_stays_hidden(self) -> None:
        _, token = self._create_link()
        phone = _phone()
        self._submit(token, phone)
        application_id = fetch_one(
            "SELECT id FROM member_enrollment_applications WHERE phone_hash=?",
            (phone_hash(phone),),
        )["id"]
        suffix = uuid4().hex[:8]
        username = f"enrollment-regional-{suffix}"
        regional_password = secrets.token_urlsafe(24)
        create_user(
            self.admin_id,
            username=username,
            display_name="入塾申请区域审核员",
            password=regional_password,
            roles=["regional_manager"],
            scopes=[
                {"scope_type": "SUBTREE", "org_unit_id": "enrollment-test-center"}
            ],
        )
        login = self.client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": regional_password},
        )
        scoped_headers = {
            "Authorization": f"Bearer {login.json()['data']['access_token']}"
        }
        unassigned = self.client.get(
            "/api/v1/enrollment-applications", headers=scoped_headers
        )
        self.assertEqual(unassigned.status_code, 200, unassigned.text)
        self.assertEqual(unassigned.json()["data"], [])
        forbidden = self.client.get(
            f"/api/v1/enrollment-applications/{application_id}",
            headers=scoped_headers,
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.text)

        central_username = f"enrollment-central-{suffix}"
        central_password = secrets.token_urlsafe(24)
        create_user(
            self.admin_id,
            username=central_username,
            display_name="入塾申请全局审核员",
            password=central_password,
            roles=["operations_admin"],
            scopes=[
                {"scope_type": "SUBTREE", "org_unit_id": "enrollment-test-center"}
            ],
        )
        central_login = self.client.post(
            "/api/v1/auth/login",
            json={
                "username": central_username,
                "password": central_password,
            },
        )
        central_headers = {
            "Authorization": f"Bearer {central_login.json()['data']['access_token']}"
        }
        central_list = self.client.get(
            "/api/v1/enrollment-applications", headers=central_headers
        )
        self.assertEqual(central_list.status_code, 200, central_list.text)
        self.assertEqual(central_list.json()["data"][0]["id"], application_id)
        self.assertEqual(central_list.json()["data"][0]["phone"], phone)

        assigned = self.client.patch(
            f"/api/v1/enrollment-applications/{application_id}/review",
            headers=self.headers,
            json={"decision": "SAVE", "org_unit_id": "enrollment-test-center"},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        visible = self.client.get(
            f"/api/v1/enrollment-applications/{application_id}",
            headers=scoped_headers,
        )
        self.assertEqual(visible.status_code, 200, visible.text)
        data = visible.json()["data"]
        self.assertTrue(data["has_enterprise_financial_data"])
        self.assertFalse(data["financial_fields_visible"])
        self.assertIsNone(data["annual_sales"])
        self.assertEqual(data["phone"], phone)
        self.assertEqual(data["company_address"], "苏州工业园区测试路1号")
        self.assertEqual(data["employee_count"], 20)
        self.assertNotIn("phone_ciphertext", data)
        self.assertNotIn("phone_hash", data)

    def test_link_rotation_invalidates_old_token_and_never_returns_hash(self) -> None:
        link_id, old_token = self._create_link()
        stored = fetch_one(
            "SELECT token_hash FROM member_enrollment_links WHERE id=?", (link_id,)
        )
        self.assertNotEqual(stored["token_hash"], old_token)
        active = self.client.get(
            "/api/v1/enrollment-links/active", headers=self.headers
        )
        self.assertNotIn("token_hash", active.json()["data"])
        self.assertNotIn("raw_token", active.json()["data"])

        rotated = self.client.post(
            f"/api/v1/enrollment-links/{link_id}/rotate", headers=self.headers
        )
        self.assertEqual(rotated.status_code, 200, rotated.text)
        new_token = rotated.json()["data"]["raw_token"]
        self.assertLessEqual(len(new_token), 32)
        self.assertEqual(
            self.client.get(f"/api/v1/public/enrollment/{old_token}").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/api/v1/public/enrollment/{new_token}").status_code,
            200,
        )
        disabled = self.client.post(
            f"/api/v1/enrollment-links/{link_id}/disable", headers=self.headers
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertEqual(
            self.client.get(f"/api/v1/public/enrollment/{new_token}").status_code,
            404,
        )

    def test_mini_program_code_uses_short_scene_and_does_not_persist_image(self) -> None:
        link_id, token = self._create_link()
        with patch.dict(
            os.environ,
            {
                "WECHAT_MINIPROGRAM_APP_ID": "test-app-id",
                "WECHAT_MINIPROGRAM_APP_SECRET": "test-app-secret",
                "WECHAT_MINIPROGRAM_PAGE": "pages/enrollment/index",
            },
            clear=False,
        ), patch("app.services.enrollment.httpx.Client") as client_class:
            client = client_class.return_value.__enter__.return_value
            client.get.return_value = httpx.Response(
                200, json={"access_token": "test-access-token"}
            )
            client.post.return_value = httpx.Response(
                200,
                content=b"synthetic-png",
                headers={"content-type": "image/png"},
            )
            response = self.client.post(
                f"/api/v1/enrollment-links/{link_id}/mini-program-code",
                headers=self.headers,
                json={"raw_token": token},
            )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertTrue(data["image_data_url"].startswith("data:image/png;base64,"))
        self.assertNotIn("raw_token", data)
        self.assertNotIn("access_token", data)
        call = client.post.call_args
        self.assertEqual(call.kwargs["params"], {"access_token": "test-access-token"})
        self.assertEqual(call.kwargs["json"]["scene"], token)
        self.assertEqual(call.kwargs["json"]["page"], "pages/enrollment/index")
        audit = fetch_one(
            "SELECT after_json FROM audit_logs "
            "WHERE action='enrollment.miniprogram_code.generate' "
            "ORDER BY id DESC LIMIT 1"
        )
        self.assertIn("pages/enrollment/index", audit["after_json"])
        self.assertNotIn(token, audit["after_json"])

    def test_public_submit_is_rate_limited_by_link_and_client_without_storing_ip(self) -> None:
        _, token = self._create_link()
        phone = _phone()
        limit = get_settings().public_enrollment_rate_limit
        for _ in range(limit):
            response = self._submit(token, phone)
            self.assertEqual(response.status_code, 200, response.text)
        limited = self._submit(token, phone)
        self.assertEqual(limited.status_code, 429, limited.text)
        guard = fetch_one(
            "SELECT guard_key, attempt_count FROM member_enrollment_submission_guards"
        )
        self.assertEqual(len(guard["guard_key"]), 64)
        self.assertEqual(guard["attempt_count"], limit)
        self.assertNotIn("testclient", guard["guard_key"])

    def test_public_submit_is_not_whitelisted_in_read_only_deployment(self) -> None:
        self.assertFalse(
            read_only_request_allowed(
                "POST", "/api/v1/public/enrollment/not-a-real-token"
            )
        )
