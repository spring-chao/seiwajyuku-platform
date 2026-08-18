from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPOSITORY_ROOT / "apps" / "platform-api"
sys.path.insert(0, str(API_ROOT))

from app.core.settings import get_settings  # noqa: E402
from app.db import execute, fetch_one, transaction  # noqa: E402
from app.migrations import run_migrations  # noqa: E402
from app.services.followup_invitations import create_invitation  # noqa: E402
from app.services.followups import create_task  # noqa: E402
from app.services.iam import create_user, seed_iam, user_context  # noqa: E402
from app.services.identity_admin import (  # noqa: E402
    create_employment,
    create_technical_assignment,
    create_volunteer_appointment,
    initialize_person_link,
)
from app.services.iam import accessible_org_ids  # noqa: E402
from app.services.members import create_member, list_members  # noqa: E402


PILOT_USERS = {
    "ops": ("pilot-ops", "试点专职同仁", "PILOT_OPS_PASSWORD"),
    "center_director": (
        "pilot-ops-center-director",
        "试点双分中心负责人",
        "PILOT_OPS_CENTER_DIRECTOR_PASSWORD",
    ),
    "primary": ("pilot-volunteer", "试点担当志工", "PILOT_VOLUNTEER_PASSWORD"),
    "companion": (
        "pilot-companion",
        "试点同行志工",
        "PILOT_COMPANION_PASSWORD",
    ),
    "technical": (
        "pilot-technical",
        "试点技术管理员",
        "PILOT_TECHNICAL_PASSWORD",
    ),
}

PILOT_CENTER_IDS = {
    "kunshan": "pilot-kunshan-center",
    "wujiang": "pilot-wujiang-center",
    "outside": "pilot-outside-center",
}


def _assert_safe_target() -> None:
    settings = get_settings()
    if settings.app_env not in {"dev", "test", "staging"}:
        raise RuntimeError("试点种子只允许在 dev/test/staging 环境运行")
    if not settings.database_url.startswith("sqlite:///"):
        raise RuntimeError("本地试点种子只允许使用隔离 SQLite 数据库")
    database_path = Path(
        settings.database_url.removeprefix("sqlite:///")
    ).resolve()
    if "pilot" not in database_path.name.lower():
        raise RuntimeError("隔离数据库文件名必须包含 pilot")
    if settings.is_production or settings.allow_production_mutations:
        raise RuntimeError("本地试点禁止生产环境或生产写入开关")
    if not (
        settings.identity_authorization_enabled
        and settings.identity_admin_writes_enabled
        and settings.volunteer_service_invitations_enabled
    ):
        raise RuntimeError("本地试点必须显式开启身份、任职写入和服务邀请开关")


def _required_password(environment_name: str) -> str:
    password = os.getenv(environment_name, "")
    if len(password) < 12:
        raise RuntimeError(f"{environment_name} 必须通过环境变量提供且至少 12 位")
    return password


def _ensure_orgs() -> None:
    now = datetime.now(UTC).isoformat()
    orgs = [
        (
            "pilot-center",
            "PILOT_CENTER",
            "隔离试点分中心",
            "REGIONAL_CENTER",
            "org-suzhou",
        ),
        (
            "pilot-class",
            "PILOT_CLASS",
            "隔离试点班级",
            "CLASS",
            "pilot-center",
        ),
        (
            "pilot-group",
            "PILOT_GROUP",
            "隔离试点小组",
            "GROUP",
            "pilot-class",
        ),
        (
            PILOT_CENTER_IDS["kunshan"],
            "PILOT_KUNSHAN_CENTER",
            "昆山分中心（隔离试点）",
            "REGIONAL_CENTER",
            "org-suzhou",
        ),
        (
            "pilot-kunshan-class",
            "PILOT_KUNSHAN_CLASS",
            "昆山试点班级",
            "CLASS",
            PILOT_CENTER_IDS["kunshan"],
        ),
        (
            PILOT_CENTER_IDS["wujiang"],
            "PILOT_WUJIANG_CENTER",
            "吴江分中心（隔离试点）",
            "REGIONAL_CENTER",
            "org-suzhou",
        ),
        (
            "pilot-wujiang-class",
            "PILOT_WUJIANG_CLASS",
            "吴江试点班级",
            "CLASS",
            PILOT_CENTER_IDS["wujiang"],
        ),
        (
            PILOT_CENTER_IDS["outside"],
            "PILOT_OUTSIDE_CENTER",
            "未授权分中心（隔离试点）",
            "REGIONAL_CENTER",
            "org-suzhou",
        ),
        (
            "pilot-outside-class",
            "PILOT_OUTSIDE_CLASS",
            "未授权试点班级",
            "CLASS",
            PILOT_CENTER_IDS["outside"],
        ),
    ]
    with transaction() as connection:
        for org_id, code, name, unit_type, parent_id in orgs:
            if execute(
                connection, "SELECT id FROM org_units WHERE id=?", (org_id,)
            ).fetchone():
                continue
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
                "is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (org_id, code, name, unit_type, parent_id, now, now),
            )


def _ensure_user(
    actor_user_id: int,
    *,
    username: str,
    display_name: str,
    password_environment_name: str,
) -> int:
    existing = fetch_one("SELECT id FROM app_users WHERE username=?", (username,))
    if existing:
        return int(existing["id"])
    return create_user(
        actor_user_id,
        username=username,
        display_name=display_name,
        password=_required_password(password_environment_name),
        roles=[],
        scopes=[],
    )


def _ensure_person(actor_user_id: int, user_id: int) -> None:
    if fetch_one(
        "SELECT person_id FROM account_person_links WHERE user_id=?", (user_id,)
    ):
        return
    initialize_person_link(
        actor_user_id,
        user_id,
        source_reference="approved-local-pilot",
        confirmation_note="隔离环境合成账号，仅用于网页试点验证",
    )


def _ensure_assignments(actor_user_id: int, users: dict[str, int]) -> tuple[str, str]:
    now = datetime.now(UTC)
    starts_at = now.isoformat()
    ends_at = (now + timedelta(days=30)).isoformat()
    if not fetch_one(
        "SELECT oe.id FROM operations_employments oe "
        "JOIN account_person_links apl ON apl.person_id=oe.person_id "
        "WHERE apl.user_id=? AND oe.employment_status='ACTIVE'",
        (users["ops"],),
    ):
        create_employment(
            actor_user_id,
            users["ops"],
            position_key="ops_center_learning",
            started_on=starts_at,
            ended_on=ends_at,
            service_responsibilities=[
                {"scope_type": "SUBTREE", "org_unit_id": "pilot-center"}
            ],
            source_reference="approved-local-pilot",
            confirmation_note="隔离环境合成专职岗位和服务范围验证",
        )
    if not fetch_one(
        "SELECT oe.id FROM operations_employments oe "
        "JOIN account_person_links apl ON apl.person_id=oe.person_id "
        "WHERE apl.user_id=? AND oe.employment_status='ACTIVE'",
        (users["center_director"],),
    ):
        create_employment(
            actor_user_id,
            users["center_director"],
            position_key="ops_center_director",
            started_on=starts_at,
            ended_on=ends_at,
            service_responsibilities=[
                {"scope_type": "SUBTREE", "org_unit_id": PILOT_CENTER_IDS["kunshan"]},
                {"scope_type": "SUBTREE", "org_unit_id": PILOT_CENTER_IDS["wujiang"]},
            ],
            source_reference="approved-local-pilot-20260814",
            confirmation_note="合成双分中心负责人范围验证；业务负责人使用脱敏岗位别名",
        )
    volunteer_assignments = (
        ("primary", "volunteer_regional_service", "pilot-center", "SUBTREE"),
        ("companion", "volunteer_class_committee", "pilot-class", "UNIT"),
    )
    for key, appointment_key, org_unit_id, scope_type in volunteer_assignments:
        if fetch_one(
            "SELECT va.id FROM volunteer_appointments va "
            "JOIN account_person_links apl ON apl.person_id=va.person_id "
            "WHERE apl.user_id=? AND va.appointment_key=? "
            "AND va.org_unit_id=? AND va.status='ACTIVE'",
            (users[key], appointment_key, org_unit_id),
        ):
            continue
        create_volunteer_appointment(
            actor_user_id,
            users[key],
            appointment_key=appointment_key,
            org_unit_id=org_unit_id,
            scope_type=scope_type,
            starts_at=starts_at,
            ends_at=ends_at,
            source_reference="approved-local-pilot",
            confirmation_note="隔离环境合成志工任职和组织范围验证",
        )
    if not fetch_one(
        "SELECT ta.id FROM technical_admin_assignments ta "
        "JOIN account_person_links apl ON apl.person_id=ta.person_id "
        "WHERE apl.user_id=? AND ta.status='ACTIVE'",
        (users["technical"],),
    ):
        create_technical_assignment(
            actor_user_id,
            users["technical"],
            assignment_purpose="隔离环境系统配置与技术安全验证",
            starts_at=starts_at,
            ends_at=ends_at,
            source_reference="approved-local-pilot",
            confirmation_note="隔离环境合成技术职责和最小权限验证",
        )
    return starts_at, ends_at


def _ensure_member(actor_user_id: int) -> int:
    existing = fetch_one(
        "SELECT id FROM members WHERE member_code='PILOT-MEMBER-001'"
    )
    if existing:
        return int(existing["id"])
    return create_member(
        actor_user_id,
        member_code="PILOT-MEMBER-001",
        name="隔离试点学长",
        # The primary organization is the regional center.  Class and group
        # membership must be supplied through formal organization IDs rather
        # than by pretending the class is the member's primary center.
        org_unit_id="pilot-center",
        development_org_unit_id="pilot-center",
        phone="13000000000",
        company_name="隔离试点企业",
        class_org_unit_id="pilot-class",
        group_org_unit_id="pilot-group",
        notes="纯合成数据，不对应真实个人或企业",
    )


def _ensure_scope_members(actor_user_id: int) -> dict[str, int]:
    """Create only synthetic records used to prove the two-center boundary."""
    members: dict[str, int] = {}
    for key, center_id, class_id in (
        ("kunshan", PILOT_CENTER_IDS["kunshan"], "pilot-kunshan-class"),
        ("wujiang", PILOT_CENTER_IDS["wujiang"], "pilot-wujiang-class"),
        ("outside", PILOT_CENTER_IDS["outside"], "pilot-outside-class"),
    ):
        member_code = f"PILOT-SCOPE-{key.upper()}"
        existing = fetch_one(
            "SELECT id FROM members WHERE member_code=?", (member_code,)
        )
        if existing:
            members[key] = int(existing["id"])
            continue
        members[key] = create_member(
            actor_user_id,
            member_code=member_code,
            name=f"{key}-scope-synthetic",
            org_unit_id=center_id,
            development_org_unit_id=center_id,
            phone=None,
            class_org_unit_id=class_id,
            notes="纯合成范围验证数据，不对应真实个人或企业",
        )
    return members


def _ensure_invitation(
    ops_user_id: int, volunteer_user_id: int, member_id: int
) -> tuple[int, int]:
    existing = fetch_one(
        "SELECT t.id AS task_id, i.id AS invitation_id "
        "FROM followup_tasks t JOIN followup_service_invitations i ON i.task_id=t.id "
        "WHERE t.member_id=? AND t.created_by=? "
        "AND i.invitation_type='ASSIGNEE' ORDER BY i.id DESC LIMIT 1",
        (member_id, ops_user_id),
    )
    if existing:
        return int(existing["task_id"]), int(existing["invitation_id"])
    now = datetime.now(UTC)
    task_id = create_task(
        ops_user_id,
        member_id=member_id,
        task_type="CARE",
        service_purpose="体验邀请担当、时间调整、暂时无法参与和同行协力",
        assigned_user_id=volunteer_user_id,
        due_at=(now + timedelta(days=7)).isoformat(),
        invitation_mode=True,
        invitation_message="想邀请您一起体验这项温暖的服务协同",
        invitation_valid_until=(now + timedelta(days=3)).isoformat(),
    )
    invitation = fetch_one(
        "SELECT id FROM followup_service_invitations "
        "WHERE task_id=? AND invitation_type='ASSIGNEE'",
        (task_id,),
    )
    return task_id, int(invitation["id"])


def _ensure_companion_invitation(
    primary_user_id: int, companion_user_id: int, member_id: int
) -> tuple[int, int]:
    """Pre-seed an independent companion scenario for first-login verification."""
    existing = fetch_one(
        "SELECT t.id AS task_id, i.id AS invitation_id "
        "FROM followup_tasks t JOIN followup_service_invitations i ON i.task_id=t.id "
        "WHERE t.member_id=? AND t.created_by=? "
        "AND i.invitation_type='COMPANION' AND i.invited_user_id=? "
        "ORDER BY i.id DESC LIMIT 1",
        (member_id, primary_user_id, companion_user_id),
    )
    if existing:
        return int(existing["task_id"]), int(existing["invitation_id"])
    now = datetime.now(UTC)
    task_id = create_task(
        primary_user_id,
        member_id=member_id,
        task_type="CARE",
        service_purpose="体验同行志工的最小必要服务记录权限",
        assigned_user_id=primary_user_id,
        due_at=(now + timedelta(days=7)).isoformat(),
    )
    invitation_id = create_invitation(
        task_id,
        primary_user_id,
        invited_user_id=companion_user_id,
        invitation_type="COMPANION",
        invitation_message="邀请您同行协力，共同温暖地完成这项服务",
        proposed_due_at=(now + timedelta(days=7)).isoformat(),
        valid_until=(now + timedelta(days=3)).isoformat(),
    )
    return task_id, invitation_id


def main() -> int:
    _assert_safe_target()
    for _, _, environment_name in PILOT_USERS.values():
        _required_password(environment_name)
    if len(os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")) < 12:
        raise RuntimeError("BOOTSTRAP_ADMIN_PASSWORD 必须至少 12 位")
    run_migrations()
    seed_iam()
    settings = get_settings()
    admin = fetch_one(
        "SELECT id FROM app_users WHERE username=?",
        (settings.bootstrap_admin_username,),
    )
    if not admin:
        raise RuntimeError("隔离试点管理员初始化失败")
    admin_id = int(admin["id"])
    _ensure_orgs()
    users = {
        key: _ensure_user(
            admin_id,
            username=username,
            display_name=display_name,
            password_environment_name=password_environment_name,
        )
        for key, (
            username,
            display_name,
            password_environment_name,
        ) in PILOT_USERS.items()
    }
    for user_id in users.values():
        _ensure_person(admin_id, user_id)
    pilot_starts_at, pilot_ends_at = _ensure_assignments(admin_id, users)
    member_id = _ensure_member(admin_id)
    scope_member_ids = _ensure_scope_members(admin_id)
    task_id, invitation_id = _ensure_invitation(
        users["ops"], users["primary"], member_id
    )
    companion_task_id, companion_invitation_id = _ensure_companion_invitation(
        users["primary"], users["companion"], member_id
    )
    contexts = {key: user_context(user_id) for key, user_id in users.items()}
    if "followups:manage" not in contexts["ops"]["permissions"]:
        raise AssertionError("试点专职账号缺少服务事项权限")
    if contexts["primary"]["language_context"] != "VOLUNTEER":
        raise AssertionError("担当志工未进入志工语言语境")
    if "followups:manage" not in contexts["companion"]["permissions"]:
        raise AssertionError("同行志工缺少服务协同权限")
    if any(
        permission in contexts["technical"]["permissions"]
        for permission in ("members:read", "followups:manage", "contact:reveal")
    ):
        raise AssertionError("技术管理员获得了业务数据权限")
    director_allowed = accessible_org_ids(users["center_director"])
    expected_director_orgs = {
        PILOT_CENTER_IDS["kunshan"],
        "pilot-kunshan-class",
        PILOT_CENTER_IDS["wujiang"],
        "pilot-wujiang-class",
    }
    if director_allowed is None or not expected_director_orgs.issubset(director_allowed):
        raise AssertionError("双分中心负责人缺少已确认的 SUBTREE 服务范围")
    if PILOT_CENTER_IDS["outside"] in director_allowed:
        raise AssertionError("双分中心负责人错误获得未授权分中心范围")
    visible_scope_members = {
        row["member_code"] for row in list_members(users["center_director"])
    }
    expected_member_codes = {
        "PILOT-SCOPE-KUNSHAN",
        "PILOT-SCOPE-WUJIANG",
    }
    if not expected_member_codes.issubset(visible_scope_members):
        raise AssertionError("双分中心负责人无法查看已授权分中心的合成学员")
    if "PILOT-SCOPE-OUTSIDE" in visible_scope_members:
        raise AssertionError("双分中心负责人可查看未授权分中心的合成学员")
    employment_audit = fetch_one(
        "SELECT id FROM audit_logs WHERE action='identity.employment.create' "
        "AND purpose=? ORDER BY id DESC LIMIT 1",
        ("合成双分中心负责人范围验证；业务负责人使用脱敏岗位别名",),
    )
    if not employment_audit:
        raise AssertionError("双分中心负责人任职缺少审计记录")
    print(
        json.dumps(
            {
                "database": get_settings().database_url,
                "accounts": {
                    key: {
                        "id": users[key],
                        "username": PILOT_USERS[key][0],
                        "language_context": contexts[key]["language_context"],
                        "roles": contexts[key]["roles"],
                    }
                    for key in users
                },
                "member_id": member_id,
                "scope_member_ids": scope_member_ids,
                "pilot_window": {
                    "starts_at": pilot_starts_at,
                    "ends_at": pilot_ends_at,
                    "duration_days": 30,
                },
                "two_center_scope": {
                    "position_key": "ops_center_director",
                    "responsibilities": [
                        {"org_unit_id": PILOT_CENTER_IDS["kunshan"], "scope_type": "SUBTREE"},
                        {"org_unit_id": PILOT_CENTER_IDS["wujiang"], "scope_type": "SUBTREE"},
                    ],
                    "excluded_org_unit_id": PILOT_CENTER_IDS["outside"],
                },
                "rollback": {
                    "owner_account": "admin",
                    "method": "destroy-disposable-sqlite-database",
                },
                "task_id": task_id,
                "invitation_id": invitation_id,
                "companion_task_id": companion_task_id,
                "companion_invitation_id": companion_invitation_id,
                "synthetic_data_only": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
