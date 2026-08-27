"""Seed a disposable V1.2 MVP-A/B browser/mini-program UX fixture.

This script is deliberately limited to an isolated dev/test SQLite database.
It creates synthetic identities, one published learning-cycle fixture, a few
home/cross-group members, and an active enrollment link so the local API and
the TEST mini-program can be exercised end to end without touching production.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.privacy import protected_phone
from app.core.settings import get_settings
from app.db import execute, fetch_one, transaction
from app.migrations import run_migrations
from app.services.enrollment import create_enrollment_link
from app.services.iam import seed_iam


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _require_isolated_target() -> None:
    settings = get_settings()
    if settings.app_env not in {"dev", "test"}:
        raise RuntimeError("V1.2 UX fixture 仅允许 dev/test 环境")
    if not settings.database_url.startswith("sqlite:///"):
        raise RuntimeError("V1.2 UX fixture 仅允许隔离 SQLite 数据库")
    db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    if "pilot" not in db_path.name.lower() or "ux" not in db_path.name.lower():
        raise RuntimeError("隔离数据库文件名必须同时包含 pilot 和 ux")
    if settings.allow_production_mutations or settings.is_production:
        raise RuntimeError("V1.2 UX fixture 禁止生产写入开关")
    if not settings.wechat_local_test_mode:
        raise RuntimeError("必须显式开启 WECHAT_LOCAL_TEST_MODE")
    if not (
        settings.identity_authorization_enabled
        and settings.wechat_member_binding_enabled
        and settings.study_meeting_submission_enabled
    ):
        raise RuntimeError("必须开启本地身份授权、微信绑定和学习会提交开关")


def _insert_member(
    connection,
    *,
    code: str,
    name: str,
    class_id: str,
    now: str,
    phone: str | None = None,
) -> int:
    fields = protected_phone(phone) if phone else {}
    cursor = execute(
        connection,
        "INSERT INTO members(member_code, name, org_unit_id, status, "
        "phone_ciphertext, phone_hash, phone_last4, phone_masked, created_at, updated_at) "
        "VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?)",
        (
            code,
            name,
            class_id,
            fields.get("phone_ciphertext"),
            fields.get("phone_hash"),
            fields.get("phone_last4"),
            fields.get("phone_masked"),
            now,
            now,
        ),
    )
    return int(cursor.lastrowid)


def _relation(connection, member_id: int, org_id: str, relation_type: str, now: str) -> None:
    execute(
        connection,
        "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, "
        "is_primary, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
        (member_id, org_id, relation_type, now, now),
    )


def main() -> int:
    _require_isolated_target()
    run_migrations()
    seed_iam()
    suffix = uuid.uuid4().hex[:8]
    center_id = f"v12-ux-center-{suffix}"
    class_id = f"v12-ux-class-{suffix}"
    group_id = f"v12-ux-group-{suffix}"
    other_group_id = f"v12-ux-group-other-{suffix}"
    other_class_id = f"v12-ux-class-other-{suffix}"
    now = _stamp()
    starts_at = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    ends_at = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    leader_phone = "13800000001"
    counselor_phone = "13800000002"

    with transaction() as connection:
        for org_id, code, name, unit_type, parent_id in (
            (center_id, f"V12_UX_CENTER_{suffix}", "V1.2本地体验中心", "REGIONAL_CENTER", "org-suzhou"),
            (class_id, f"V12_UX_CLASS_{suffix}", "V1.2本地体验班", "CLASS", center_id),
            (group_id, f"V12_UX_GROUP_{suffix}", "第一小组", "GROUP", class_id),
            (other_group_id, f"V12_UX_GROUP_OTHER_{suffix}", "第二小组", "GROUP", class_id),
            (other_class_id, f"V12_UX_CLASS_OTHER_{suffix}", "V1.2其他班", "CLASS", center_id),
        ):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (org_id, code, name, unit_type, parent_id, now, now),
            )

        leader_id = _insert_member(
            connection,
            code=f"V12-UX-LEADER-{suffix}",
            name="V1.2组长",
            class_id=class_id,
            now=now,
            phone=leader_phone,
        )
        _relation(connection, leader_id, group_id, "STUDY_GROUP", now)
        _relation(connection, leader_id, class_id, "STUDY_CLASS", now)
        leader_person_id = f"v12-ux-leader-person-{suffix}"
        execute(
            connection,
            "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', ?, ?)",
            (leader_person_id, "V1.2组长", now, now),
        )
        execute(
            connection,
            "INSERT INTO member_identities(member_id, person_id, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', ?, ?)",
            (leader_id, leader_person_id, now, now),
        )
        execute(
            connection,
            "INSERT INTO volunteer_appointments(person_id, appointment_key, org_unit_id, scope_type, "
            "starts_at, ends_at, status, source_reference, created_at, updated_at) "
            "VALUES (?, 'volunteer_group_leader', ?, 'UNIT', ?, ?, 'ACTIVE', 'v12-local-ux', ?, ?)",
            (leader_person_id, group_id, starts_at, ends_at, now, now),
        )

        home_ids = [leader_id]
        for index in range(1, 5):
            member_id = _insert_member(
                connection,
                code=f"V12-UX-HOME-{suffix}-{index}",
                name=f"V1.2本组学长{index}",
                class_id=class_id,
                now=now,
            )
            home_ids.append(member_id)
            _relation(connection, member_id, group_id, "STUDY_GROUP", now)
            _relation(connection, member_id, class_id, "STUDY_CLASS", now)

        cross_ids = []
        for index, name in ((1, "V1.2跨组学长1"), (2, "V1.2跨组学长2")):
            member_id = _insert_member(
                connection,
                code=f"V12-UX-CROSS-{suffix}-{index}",
                name=name,
                class_id=class_id,
                now=now,
            )
            cross_ids.append(member_id)
            _relation(connection, member_id, other_group_id, "STUDY_GROUP", now)
            _relation(connection, member_id, class_id, "STUDY_CLASS", now)

        counselor_id = _insert_member(
            connection,
            code=f"V12-UX-COUNSELOR-{suffix}",
            name="V1.2辅导员",
            class_id=class_id,
            now=now,
            phone=counselor_phone,
        )
        _relation(connection, counselor_id, class_id, "STUDY_CLASS", now)
        counselor_person_id = f"v12-ux-counselor-person-{suffix}"
        execute(
            connection,
            "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', ?, ?)",
            (counselor_person_id, "V1.2辅导员", now, now),
        )
        execute(
            connection,
            "INSERT INTO member_identities(member_id, person_id, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', ?, ?)",
            (counselor_id, counselor_person_id, now, now),
        )
        execute(
            connection,
            "INSERT INTO volunteer_appointments(person_id, appointment_key, org_unit_id, scope_type, "
            "starts_at, ends_at, status, source_reference, created_at, updated_at) "
            "VALUES (?, 'volunteer_class_counselor', ?, 'UNIT', ?, ?, 'ACTIVE', 'v12-local-ux', ?, ?)",
            (counselor_person_id, class_id, starts_at, ends_at, now, now),
        )

        plan_cursor = execute(
            connection,
            "INSERT INTO learning_plan_versions(plan_key, plan_name, version_label, duration_cycles, "
            "status, created_at, updated_at) VALUES (?, 'V1.2本地体验计划', ?, 36, 'PUBLISHED', ?, ?)",
            (f"V12_UX_{suffix}", f"2026-v12-ux-{suffix}", now, now),
        )
        plan_id = int(plan_cursor.lastrowid)
        cycle_cursor = execute(
            connection,
            "INSERT INTO learning_plan_cycles(plan_version_id, cohort_month, cycle_index, year_index, "
            "cycle_label, created_at, updated_at) VALUES (?, 1, 1, 1, '第1周期', ?, ?)",
            (plan_id, now, now),
        )
        plan_cycle_id = int(cycle_cursor.lastrowid)
        binding_cursor = execute(
            connection,
            "INSERT INTO class_learning_bindings(class_org_unit_id, plan_version_id, cohort_month, "
            "started_at, status, created_at, updated_at) VALUES (?, ?, 1, ?, 'ACTIVE', ?, ?)",
            (class_id, plan_id, now, now, now),
        )
        binding_id = int(binding_cursor.lastrowid)
        cycle_cursor = execute(
            connection,
            "INSERT INTO class_learning_cycles(binding_id, class_org_unit_id, learning_cycle_index, "
            "plan_cycle_id, opened_at, class_meeting_status, group_meeting_policy, cycle_status, "
            "created_at, updated_at) VALUES (?, ?, 1, ?, ?, 'PLANNED', 'REQUIRED', 'OPEN', ?, ?)",
            (binding_id, class_id, plan_cycle_id, now, now, now),
        )
        learning_cycle_id = int(cycle_cursor.lastrowid)

    admin_id = int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])
    link = create_enrollment_link(admin_id, "V1.2本地新学长体验入口")
    catalog = json.loads(
        (Path(__file__).resolve().parents[1] / "data/learning-plans/course-credit-catalog-2026.json").read_text(
            encoding="utf-8"
        )
    )["entries"]
    print(
        json.dumps(
            {
                "database": get_settings().database_url,
                "synthetic_data_only": True,
                "admin": {"username": "admin"},
                "leader": {
                    "name": "V1.2组长",
                    "phone": leader_phone,
                    "member_id": leader_id,
                    "class_id": class_id,
                    "group_id": group_id,
                },
                "counselor": {
                    "name": "V1.2辅导员",
                    "phone": counselor_phone,
                    "member_id": counselor_id,
                    "class_id": class_id,
                },
                "home_member_ids": home_ids,
                "cross_member_ids": cross_ids,
                "learning_cycle_id": learning_cycle_id,
                "configured_course": next(item for item in catalog if item["status"] == "CONFIGURED"),
                "pending_course": next(item for item in catalog if item["status"] == "PENDING"),
                "enrollment_link": {"id": link["id"], "name": link["name"]},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
