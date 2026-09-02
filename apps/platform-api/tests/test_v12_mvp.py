from __future__ import annotations

import os
import io
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from PIL import Image

from app.core.privacy import protected_phone
from app.db import execute, fetch_all, fetch_one, transaction
from app.main import app
from app.services.enrollment import create_enrollment_link, rotate_enrollment_link
from app.services.wechat_identity import exchange_wechat_code


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _phone() -> str:
    return f"138{uuid4().int % 100_000_000:08d}"


def _seed_group_leader_fixture() -> dict:
    suffix = uuid4().hex[:10]
    center_id = f"v12-center-{suffix}"
    class_id = f"v12-class-{suffix}"
    group_id = f"v12-group-{suffix}"
    other_group_id = f"v12-group-other-{suffix}"
    other_class_id = f"v12-class-other-{suffix}"
    now = _stamp()
    phone = _phone()
    phone_fields = protected_phone(phone)
    with transaction() as connection:
        for unit_id, code, name, unit_type, parent_id in (
            (center_id, f"V12_CENTER_{suffix}", "V1.2测试中心", "REGIONAL_CENTER", "org-suzhou"),
            (class_id, f"V12_CLASS_{suffix}", "V1.2测试班", "CLASS", center_id),
            (group_id, f"V12_GROUP_{suffix}", "第一小组", "GROUP", class_id),
            (other_group_id, f"V12_GROUP_OTHER_{suffix}", "第二小组", "GROUP", class_id),
            (other_class_id, f"V12_CLASS_OTHER_{suffix}", "V1.2其他班", "CLASS", center_id),
        ):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (unit_id, code, name, unit_type, parent_id, now, now),
            )
        member_cursor = execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, phone_ciphertext, phone_hash, phone_last4, phone_masked, created_at, updated_at) "
            "VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?)",
            (
                f"V12M-{suffix}",
                "V1.2组长",
                class_id,
                phone_fields["phone_ciphertext"],
                phone_fields["phone_hash"],
                phone_fields["phone_last4"],
                phone_fields["phone_masked"],
                now,
                now,
            ),
        )
        member_id = int(member_cursor.lastrowid)
        other_cursor = execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, created_at, updated_at) VALUES (?, ?, ?, 'ACTIVE', ?, ?)",
            (f"V12M2-{suffix}", "V1.2跨组学长", class_id, now, now),
        )
        other_member_id = int(other_cursor.lastrowid)
        foreign_cursor = execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, created_at, updated_at) VALUES (?, ?, ?, 'ACTIVE', ?, ?)",
            (f"V12M3-{suffix}", "V1.2外班学长", other_class_id, now, now),
        )
        foreign_member_id = int(foreign_cursor.lastrowid)
        for relation_member_id, relation_group_id, relation_type in (
            (member_id, group_id, "STUDY_GROUP"),
            (member_id, class_id, "STUDY_CLASS"),
            (other_member_id, other_group_id, "STUDY_GROUP"),
            (other_member_id, class_id, "STUDY_CLASS"),
            (foreign_member_id, other_class_id, "STUDY_CLASS"),
        ):
            execute(
                connection,
                "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                (relation_member_id, relation_group_id, relation_type, now, now),
            )
        person_id = f"v12-person-{suffix}"
        execute(
            connection,
            "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) VALUES (?, ?, 'ACTIVE', ?, ?)",
            (person_id, "V1.2组长", now, now),
        )
        execute(
            connection,
            "INSERT INTO member_identities(member_id, person_id, status, created_at, updated_at) VALUES (?, ?, 'ACTIVE', ?, ?)",
            (member_id, person_id, now, now),
        )
        execute(
            connection,
            "INSERT INTO volunteer_appointments(person_id, appointment_key, org_unit_id, scope_type, starts_at, ends_at, status, source_reference, created_at, updated_at) VALUES (?, 'volunteer_group_leader', ?, 'UNIT', ?, ?, 'ACTIVE', 'v12-test', ?, ?)",
            (person_id, group_id, (datetime.now(UTC) - timedelta(days=1)).isoformat(), (datetime.now(UTC) + timedelta(days=30)).isoformat(), now, now),
        )
        plan_cursor = execute(
            connection,
            "INSERT INTO learning_plan_versions(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) VALUES (?, 'V1.2测试计划', ?, 36, 'PUBLISHED', ?, ?)",
            (f"V12_{suffix}", f"2026-v12-{suffix}", now, now),
        )
        plan_id = int(plan_cursor.lastrowid)
        cycle_cursor = execute(
            connection,
            "INSERT INTO learning_plan_cycles(plan_version_id, cohort_month, cycle_index, year_index, cycle_label, created_at, updated_at) VALUES (?, 1, 1, 1, '第1周期', ?, ?)",
            (plan_id, now, now),
        )
        plan_cycle_id = int(cycle_cursor.lastrowid)
        binding_cursor = execute(
            connection,
            "INSERT INTO class_learning_bindings(class_org_unit_id, plan_version_id, cohort_month, started_at, status, created_at, updated_at) VALUES (?, ?, 1, ?, 'ACTIVE', ?, ?)",
            (class_id, plan_id, now, now, now),
        )
        binding_id = int(binding_cursor.lastrowid)
        cycle_cursor = execute(
            connection,
            "INSERT INTO class_learning_cycles(binding_id, class_org_unit_id, learning_cycle_index, plan_cycle_id, opened_at, class_meeting_status, group_meeting_policy, cycle_status, created_at, updated_at) VALUES (?, ?, 1, ?, ?, 'PLANNED', 'REQUIRED', 'OPEN', ?, ?)",
            (binding_id, class_id, plan_cycle_id, now, now, now),
        )
        learning_cycle_id = int(cycle_cursor.lastrowid)
    return {
        "suffix": suffix,
        "class_id": class_id,
        "group_id": group_id,
        "other_group_id": other_group_id,
        "other_class_id": other_class_id,
        "member_id": member_id,
        "other_member_id": other_member_id,
        "foreign_member_id": foreign_member_id,
        "phone": phone,
        "learning_cycle_id": learning_cycle_id,
    }


def test_portal_handoff_never_exposes_long_lived_token() -> None:
    admin_id = int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])
    link = create_enrollment_link(admin_id, f"V1.2入口-{uuid4().hex[:6]}")
    with TestClient(app) as client:
        response = client.get("/api/v1/public/portal")
        assert response.status_code == 200, response.text
        entry = response.json()["data"]["enrollment_entry"]
        assert entry["handoff_token"] != link["raw_token"]
        assert "raw_token" not in entry
        form = client.get(f"/api/v1/public/enrollment/{entry['handoff_token']}")
        assert form.status_code == 200, form.text


def test_member_binding_masks_identity_and_revoke_closes_session() -> None:
    fixture = _seed_group_leader_fixture()
    with patch.dict(
        os.environ,
        {
            "WECHAT_MEMBER_BINDING_ENABLED": "true",
            "WECHAT_MINIPROGRAM_APP_ID": "v12-test-app",
            "WECHAT_MINIPROGRAM_APP_SECRET": "v12-test-secret",
        },
    ), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": "v12-test-app", "openid": f"openid-{fixture['suffix']}"},
    ):
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/wechat/member-bindings/verify",
                json={"code": "code", "name": "V1.2组长", "phone": fixture["phone"]},
            )
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["member"]["name_masked"] == "V****长"
            assert "openid" not in response.text
            assert fixture["phone"] not in response.text
            headers = {"Authorization": f"Bearer {data['access_token']}"}
            me = client.get("/api/v1/wechat/me", headers=headers)
            assert me.status_code == 200, me.text
            revoke = client.post("/api/v1/wechat/member-bindings/revoke", headers=headers)
            assert revoke.status_code == 200, revoke.text
            assert client.get("/api/v1/wechat/me", headers=headers).status_code == 401


def test_member_binding_rebinds_same_wechat_after_revoke_and_rotates_old_tokens() -> None:
    fixture = _seed_group_leader_fixture()
    other_phone = _phone()
    other_phone_fields = protected_phone(other_phone)
    with transaction() as connection:
        execute(
            connection,
            "UPDATE members SET phone_ciphertext=?, phone_hash=?, phone_last4=?, phone_masked=? WHERE id=?",
            (
                other_phone_fields["phone_ciphertext"],
                other_phone_fields["phone_hash"],
                other_phone_fields["phone_last4"],
                other_phone_fields["phone_masked"],
                fixture["other_member_id"],
            ),
        )

    appid = f"v12-rebind-{fixture['suffix']}"
    openid = f"same-wechat-{fixture['suffix']}"
    with patch.dict(
        os.environ,
        {
            "WECHAT_MEMBER_BINDING_ENABLED": "true",
            "WECHAT_LOCAL_TEST_MODE": "false",
            "WECHAT_MINIPROGRAM_APP_ID": appid,
            "WECHAT_MINIPROGRAM_APP_SECRET": "v12-test-secret",
        },
    ), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": appid, "openid": openid},
    ), TestClient(app) as client:
        def bind(name: str, phone: str):
            return client.post(
                "/api/v1/wechat/member-bindings/verify",
                json={"code": "code", "name": name, "phone": phone},
            )

        first = bind("V1.2组长", fixture["phone"])
        assert first.status_code == 200, first.text
        first_token = first.json()["data"]["access_token"]

        same_member = bind("V1.2组长", fixture["phone"])
        assert same_member.status_code == 200, same_member.text

        switch_without_revoke = bind("V1.2跨组学长", other_phone)
        assert switch_without_revoke.status_code == 400
        assert switch_without_revoke.json()["detail"] == "当前微信已绑定其他学员，请先解绑后再绑定"
        assert client.get(
            "/api/v1/wechat/me", headers={"Authorization": f"Bearer {first_token}"}
        ).json()["data"]["member"]["member_id"] == fixture["member_id"]

        revoked = client.post(
            "/api/v1/wechat/member-bindings/revoke",
            headers={"Authorization": f"Bearer {first_token}"},
        )
        assert revoked.status_code == 200, revoked.text
        assert client.get(
            "/api/v1/wechat/me", headers={"Authorization": f"Bearer {first_token}"}
        ).status_code == 401

        before_rebind = fetch_one(
            "SELECT id, member_id, status, active_slot, token_version "
            "FROM wechat_member_bindings WHERE appid=? AND openid=?",
            (appid, openid),
        )
        assert before_rebind is not None
        assert before_rebind["member_id"] == fixture["member_id"]
        assert before_rebind["status"] == "REVOKED"
        assert before_rebind["active_slot"] is None
        assert before_rebind["token_version"] == 2

        rebound = bind("V1.2跨组学长", other_phone)
        assert rebound.status_code == 200, rebound.text
        rebound_data = rebound.json()["data"]
        assert rebound_data["member"]["member_id"] == fixture["other_member_id"]
        assert client.get(
            "/api/v1/wechat/me", headers={"Authorization": f"Bearer {first_token}"}
        ).status_code == 401
        assert client.get(
            "/api/v1/wechat/me",
            headers={"Authorization": f"Bearer {rebound_data['access_token']}"},
        ).json()["data"]["member"]["member_id"] == fixture["other_member_id"]

        after_rebind = fetch_one(
            "SELECT id, member_id, status, active_slot, token_version "
            "FROM wechat_member_bindings WHERE appid=? AND openid=?",
            (appid, openid),
        )
        assert after_rebind["id"] == before_rebind["id"]
        assert after_rebind["member_id"] == fixture["other_member_id"]
        assert after_rebind["status"] == "VERIFIED"
        assert after_rebind["active_slot"] == 1
        assert after_rebind["token_version"] == 3

        audits = fetch_all(
            "SELECT action, before_json, after_json FROM audit_logs "
            "WHERE resource_type='wechat_member_binding' AND resource_id=? ORDER BY id",
            (str(after_rebind["id"]),),
        )
        rebind_audit = next(row for row in audits if row["action"] == "wechat.member_binding.rebind")
        assert json.loads(rebind_audit["before_json"]) == {
            "member_id": fixture["member_id"],
            "status": "REVOKED",
            "token_version": 2,
        }
        assert json.loads(rebind_audit["after_json"])["member_id"] == fixture["other_member_id"]
        assert json.loads(rebind_audit["after_json"])["status"] == "VERIFIED"


def test_member_binding_rejects_target_already_bound_to_another_wechat() -> None:
    fixture = _seed_group_leader_fixture()
    target_phone = _phone()
    target_phone_fields = protected_phone(target_phone)
    with transaction() as connection:
        execute(
            connection,
            "UPDATE members SET phone_ciphertext=?, phone_hash=?, phone_last4=?, phone_masked=? WHERE id=?",
            (
                target_phone_fields["phone_ciphertext"],
                target_phone_fields["phone_hash"],
                target_phone_fields["phone_last4"],
                target_phone_fields["phone_masked"],
                fixture["other_member_id"],
            ),
        )

    appid = f"v12-member-conflict-{fixture['suffix']}"
    with patch.dict(
        os.environ,
        {
            "WECHAT_MEMBER_BINDING_ENABLED": "true",
            "WECHAT_LOCAL_TEST_MODE": "false",
            "WECHAT_MINIPROGRAM_APP_ID": appid,
            "WECHAT_MINIPROGRAM_APP_SECRET": "v12-test-secret",
        },
    ), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        side_effect=[
            {"appid": appid, "openid": "wechat-owner"},
            {"appid": appid, "openid": "wechat-contender"},
        ],
    ), TestClient(app) as client:
        owned = client.post(
            "/api/v1/wechat/member-bindings/verify",
            json={"code": "owner-code", "name": "V1.2跨组学长", "phone": target_phone},
        )
        assert owned.status_code == 200, owned.text
        contender = client.post(
            "/api/v1/wechat/member-bindings/verify",
            json={"code": "contender-code", "name": "V1.2跨组学长", "phone": target_phone},
        )
        assert contender.status_code == 400
        assert contender.json()["detail"] == "该学员已有微信绑定，如需更换请联系工作人员"


def test_wechat_profile_returns_join_dates_and_safe_volunteer_history() -> None:
    fixture = _seed_group_leader_fixture()
    person_id = fetch_one(
        "SELECT person_id FROM member_identities WHERE member_id=?",
        (fixture["member_id"],),
    )["person_id"]
    now = _stamp()
    with transaction() as connection:
        execute(
            connection,
            "UPDATE members SET join_date='2022-05-06', study_start_date='2022-04-01' "
            "WHERE id=?",
            (fixture["member_id"],),
        )
        execute(
            connection,
            "UPDATE org_units SET name='卓越组' WHERE id=?",
            (fixture["group_id"],),
        )
        execute(
            connection,
            "UPDATE volunteer_appointments SET appointment_key='volunteer_group_counselor', "
            "starts_at='2026-08-01T00:00:00+00:00', ends_at=NULL, status='ACTIVE' "
            "WHERE person_id=?",
            (person_id,),
        )
        execute(
            connection,
            "INSERT INTO volunteer_appointments"
            "(person_id, appointment_key, org_unit_id, scope_type, starts_at, ends_at, "
            "status, source_reference, created_at, updated_at) "
            "VALUES (?, 'volunteer_class_monitor', ?, 'UNIT', "
            "'2025-01-01T00:00:00+00:00', '2025-12-31T00:00:00+00:00', "
            "'ENDED', 'test-private-source', ?, ?)",
            (person_id, fixture["class_id"], now, now),
        )

    with patch.dict(
        os.environ,
        {
            "WECHAT_MEMBER_BINDING_ENABLED": "true",
            "WECHAT_MINIPROGRAM_APP_ID": "v10-profile-test",
            "WECHAT_MINIPROGRAM_APP_SECRET": "v10-profile-secret",
        },
    ), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={
            "appid": "v10-profile-test",
            "openid": f"profile-{fixture['suffix']}",
        },
    ), TestClient(app) as client:
        bound = client.post(
            "/api/v1/wechat/member-bindings/verify",
            json={
                "code": "profile-code",
                "name": "V1.2组长",
                "phone": fixture["phone"],
            },
        )
        assert bound.status_code == 200, bound.text
        headers = {
            "Authorization": f"Bearer {bound.json()['data']['access_token']}"
        }
        member = client.get("/api/v1/wechat/me", headers=headers)
        assert member.status_code == 200, member.text
        profile = member.json()["data"]["member"]
        assert profile["join_date"] == "2022-05-06"
        assert profile["study_start_date"] == "2022-04-01"
        assert set(profile) == {
            "member_id",
            "name_masked",
            "phone_masked",
            "class_org_unit_id",
            "class_name",
            "study_group_org_unit_id",
            "study_group_name",
            "join_date",
            "study_start_date",
        }

        current = client.get("/api/v1/wechat/volunteer-services", headers=headers)
        assert current.status_code == 200, current.text
        assert current.json()["data"]["roles"][0]["position_name"] == "辅导员"
        assert current.json()["data"]["roles"][0]["scope_name"] == "卓越组"

        history = client.get("/api/v1/wechat/volunteer-history", headers=headers)
        assert history.status_code == 200, history.text
        appointments = history.json()["data"]["appointments"]
        assert appointments[0] == {
            "position_name": "辅导员",
            "scope_name": "卓越组",
            "status_name": "服务中",
            "starts_at": "2026-08-01T00:00:00+00:00",
            "ends_at": None,
        }
        assert appointments[1]["position_name"] == "班长"
        assert appointments[1]["status_name"] == "已结束"
        for item in appointments:
            assert not {
                "position_key",
                "source_reference",
                "person_id",
                "member_id",
                "capabilities",
            }.intersection(item)


@pytest.mark.parametrize("position", ["volunteer_group_leader", "volunteer_group_counselor", None])
def test_home_identity_is_independent_of_study_meeting_capability(position, monkeypatch) -> None:
    fixture = _seed_group_leader_fixture()
    monkeypatch.setenv("WECHAT_MEMBER_BINDING_ENABLED", "true")
    monkeypatch.setenv("STUDY_MEETING_SUBMISSION_ENABLED", "true")
    monkeypatch.setenv("IDENTITY_AUTHORIZATION_ENABLED", "true")
    monkeypatch.setenv("WECHAT_MINIPROGRAM_APP_ID", "v12-home-test")
    credits_before = fetch_one("SELECT COUNT(*) AS n FROM attendance_score_records")["n"]
    person_id = fetch_one("SELECT person_id FROM member_identities WHERE member_id=?", (fixture["member_id"],))["person_id"]
    with transaction() as connection:
        execute(connection, "UPDATE volunteer_appointments SET appointment_key=?, status=? WHERE person_id=?",
                (position or "volunteer_group_leader", "ACTIVE" if position else "ENDED", person_id))
    with patch("app.services.wechat_identity.exchange_wechat_code", return_value={
        "appid": "v12-home-test", "openid": f"home-{fixture['suffix']}"
    }), TestClient(app) as client:
        bound = client.post("/api/v1/wechat/member-bindings/verify", json={
            "code": "synthetic-code", "name": "V1.2组长", "phone": fixture["phone"]
        })
        assert bound.status_code == 200, bound.text
        headers = {"Authorization": "Bearer " + bound.json()["data"]["access_token"]}
        me = client.get("/api/v1/wechat/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["data"]["member"]["member_id"] == fixture["member_id"]
        context = client.get("/api/v1/study-meetings/context", headers=headers)
        if position:
            assert context.status_code == 200, context.text
            assert [item["group_org_unit_id"] for item in context.json()["data"]["assignments"]] == [fixture["group_id"]]
        else:
            assert context.status_code == 403, context.text
            assert client.get("/api/v1/wechat/me", headers=headers).status_code == 200
        assert client.post("/api/v1/wechat/member-bindings/revoke", headers=headers).status_code == 200
        assert client.get("/api/v1/wechat/me", headers=headers).status_code == 401
    assert fetch_one("SELECT COUNT(*) AS n FROM attendance_score_records")["n"] == credits_before


def test_study_meeting_same_class_cross_group_does_not_change_relations(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("STUDY_MEETING_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("STUDY_EVIDENCE_LOCAL_ROOT", str(tmp_path))
    fixture = _seed_group_leader_fixture()
    with patch.dict(
        os.environ,
        {
            "WECHAT_MEMBER_BINDING_ENABLED": "true",
            "STUDY_MEETING_SUBMISSION_ENABLED": "true",
            "WECHAT_MINIPROGRAM_APP_ID": "v12-test-app",
            "WECHAT_MINIPROGRAM_APP_SECRET": "v12-test-secret",
        },
    ), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": "v12-test-app", "openid": f"openid-{fixture['suffix']}"},
    ):
        with TestClient(app) as client:
            binding = client.post(
                "/api/v1/wechat/member-bindings/verify",
                json={"code": "code", "name": "V1.2组长", "phone": fixture["phone"]},
            )
            assert binding.status_code == 200, binding.text
            headers = {"Authorization": f"Bearer {binding.json()['data']['access_token']}"}
            context = client.get(
                f"/api/v1/study-meetings/context?group_org_unit_id={fixture['group_id']}",
                headers=headers,
            )
            assert context.status_code == 200, context.text
            assignment = context.json()["data"]["assignment"]
            assert assignment["current_cycle"]["learning_cycle_index"] == 1
            assert assignment["position_name"] == "组长"
            assert "courses" not in context.json()["data"]
            assert assignment["meeting_plan"]["cycle_index"] == 1
            assert assignment["meeting_plan"]["configuration_status"] == "CONFIGURED"
            created = client.post(
                "/api/v1/study-meetings",
                headers=headers,
                json={
                    "group_org_unit_id": fixture["group_id"],
                    "member_ids": [fixture["member_id"]],
                    "cross_group_member_ids": [fixture["other_member_id"]],
                    "has_course": False,
                },
            )
            assert created.status_code == 200, created.text
            session = created.json()["data"]
            photo = io.BytesIO()
            Image.new("RGB", (20, 20), "white").save(photo, "JPEG")
            uploaded = client.post(
                f"/api/v1/study-meetings/{session['id']}/evidence", headers=headers,
                files={"photo": ("photo.jpg", photo.getvalue(), "image/jpeg")},
            )
            assert uploaded.status_code == 200, uploaded.text
            submitted = client.post(
                f"/api/v1/study-meetings/{session['id']}/submit", headers=headers
            )
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()["data"]["status"] == "SUBMITTED"
            assert len(submitted.json()["data"]["cross_group_attendees"]) == 1
            relation = fetch_one(
                "SELECT org_unit_id FROM member_org_relations WHERE member_id=? AND relation_type='STUDY_GROUP'",
                (fixture["other_member_id"],),
            )
            assert relation["org_unit_id"] == fixture["other_group_id"]
            rejected = client.post(
                "/api/v1/study-meetings",
                headers=headers,
                json={
                    "group_org_unit_id": fixture["group_id"],
                    "member_ids": [fixture["member_id"]],
                    "cross_group_member_ids": [fixture["foreign_member_id"]],
                },
            )
            assert rejected.status_code == 400


def test_study_meeting_submit_rechecks_changed_attendee_status() -> None:
    fixture = _seed_group_leader_fixture()
    with patch.dict(
        os.environ,
        {
            "WECHAT_MEMBER_BINDING_ENABLED": "true",
            "STUDY_MEETING_SUBMISSION_ENABLED": "true",
            "WECHAT_MINIPROGRAM_APP_ID": "v12-test-app",
            "WECHAT_MINIPROGRAM_APP_SECRET": "v12-test-secret",
        },
    ), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": "v12-test-app", "openid": f"openid-{fixture['suffix']}"},
    ):
        with TestClient(app) as client:
            binding = client.post(
                "/api/v1/wechat/member-bindings/verify",
                json={"code": "code", "name": "V1.2组长", "phone": fixture["phone"]},
            )
            assert binding.status_code == 200, binding.text
            headers = {"Authorization": f"Bearer {binding.json()['data']['access_token']}"}
            created = client.post(
                "/api/v1/study-meetings",
                headers=headers,
                json={
                    "group_org_unit_id": fixture["group_id"],
                    "member_ids": [fixture["member_id"]],
                    "cross_group_member_ids": [fixture["other_member_id"]],
                },
            )
            assert created.status_code == 200, created.text
            session_id = created.json()["data"]["id"]
            with transaction() as connection:
                execute(
                    connection,
                    "UPDATE members SET status='INACTIVE' WHERE id=?",
                    (fixture["other_member_id"],),
                )
            submitted = client.post(
                f"/api/v1/study-meetings/{session_id}/submit", headers=headers
            )
            assert submitted.status_code == 400, submitted.text
            assert fetch_one(
                "SELECT status FROM study_meeting_sessions WHERE id=?", (session_id,)
            )["status"] == "DRAFT"


def test_portal_handoff_is_invalidated_when_the_enrollment_link_rotates() -> None:
    admin_id = int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])
    link = create_enrollment_link(admin_id, f"V1.2轮换-{uuid4().hex[:6]}")
    with TestClient(app) as client:
        handoff = client.get("/api/v1/public/portal").json()["data"]["enrollment_entry"]
        rotate_enrollment_link(admin_id, link["id"])
        response = client.get(
            f"/api/v1/public/enrollment/{handoff['handoff_token']}"
        )
        assert response.status_code == 404


def test_v12_write_flags_are_closed_by_default() -> None:
    with patch.dict(
        os.environ,
        {
            "WECHAT_MEMBER_BINDING_ENABLED": "false",
            "STUDY_MEETING_SUBMISSION_ENABLED": "false",
        },
    ):
        with TestClient(app) as client:
            assert client.post(
                "/api/v1/wechat/member-bindings/verify",
                json={"code": "code", "name": "x", "phone": "13800000000"},
            ).status_code == 404


def test_local_wechat_provider_stub_is_deterministic_and_dev_only() -> None:
    with patch.dict(
        os.environ,
        {
            "APP_ENV": "test",
            "WECHAT_LOCAL_TEST_MODE": "true",
            "WECHAT_MINIPROGRAM_APP_ID": "v12-local-test-app",
            "WECHAT_MINIPROGRAM_APP_SECRET": "",
        },
    ):
        assert exchange_wechat_code("developer-tool-code") == {
            "appid": "v12-local-test-app",
            "openid": "local-test-openid",
        }
