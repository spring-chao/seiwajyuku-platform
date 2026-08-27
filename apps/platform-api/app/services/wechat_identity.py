"""WeChat mini-program identity binding for V1.2 MVP.

The mini-program session is deliberately separate from the operations JWT.
``member_id`` remains the business identity; the binding row is only the
revocable credential that lets a WeChat session resolve that identity.
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.privacy import normalize_phone, phone_hash
from app.core.security import create_token, decode_token
from app.core.settings import get_settings
from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.volunteer_positions import STUDY_MEETING_MANAGE


WECHAT_SESSION_TOKEN_TYPE = "wechat_member_access"
WECHAT_BINDING_ROLES = {
    "volunteer_group_leader": "GROUP_LEADER",
    "volunteer_class_counselor": "CLASS_COUNSELOR",
    "group_leader": "GROUP_LEADER",
    "class_counselor": "CLASS_COUNSELOR",
}


class WeChatIdentityError(ValueError):
    """A safe, non-enumerating identity binding failure."""


class WeChatProviderError(ValueError):
    """The configured WeChat provider could not exchange a login code."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _db_timestamp(connection) -> str:
    """Use the representation accepted by both SQLite text and MySQL DATETIME."""

    current = datetime.now(UTC)
    if isinstance(connection, sqlite3.Connection):
        return current.isoformat()
    return current.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _active_relation_predicate(alias: str = "r") -> str:
    return (
        f"{alias}.relation_type=? AND "
        f"({alias}.valid_from IS NULL OR {alias}.valid_from<=?) AND "
        f"({alias}.valid_until IS NULL OR {alias}.valid_until>=?)"
    )


def _mask_name(value: str) -> str:
    name = (value or "").strip()
    if not name:
        return "*"
    if len(name) == 1:
        return f"{name}*"
    if len(name) == 2:
        return f"{name[0]}*"
    return f"{name[0]}{'*' * (len(name) - 2)}{name[-1]}"


def _member_payload(connection, member_id: int) -> dict[str, Any] | None:
    now = _now()
    row = execute(
        connection,
        "SELECT m.id, m.name, m.phone_masked, "
        "(SELECT ou.id FROM member_org_relations r JOIN org_units ou ON ou.id=r.org_unit_id "
        "WHERE r.member_id=m.id AND "
        + _active_relation_predicate("r")
        + " AND ou.is_active=1 ORDER BY r.is_primary DESC, r.id DESC LIMIT 1) AS class_org_unit_id, "
        "(SELECT ou.name FROM member_org_relations r JOIN org_units ou ON ou.id=r.org_unit_id "
        "WHERE r.member_id=m.id AND "
        + _active_relation_predicate("r")
        + " AND ou.is_active=1 ORDER BY r.is_primary DESC, r.id DESC LIMIT 1) AS class_name, "
        "(SELECT ou.id FROM member_org_relations r JOIN org_units ou ON ou.id=r.org_unit_id "
        "WHERE r.member_id=m.id AND "
        + _active_relation_predicate("r",)
        + " AND ou.is_active=1 ORDER BY r.is_primary DESC, r.id DESC LIMIT 1) AS study_group_org_unit_id, "
        "(SELECT ou.name FROM member_org_relations r JOIN org_units ou ON ou.id=r.org_unit_id "
        "WHERE r.member_id=m.id AND "
        + _active_relation_predicate("r",)
        + " AND ou.is_active=1 ORDER BY r.is_primary DESC, r.id DESC LIMIT 1) AS study_group_name "
        "FROM members m WHERE m.id=? AND m.status='ACTIVE'",
        (
            "STUDY_CLASS",
            now,
            now,
            "STUDY_CLASS",
            now,
            now,
            "STUDY_GROUP",
            now,
            now,
            "STUDY_GROUP",
            now,
            now,
            member_id,
        ),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["name_masked"] = _mask_name(result["name"])
    result["phone_masked"] = result.get("phone_masked") or ""
    return {
        "member_id": int(result["id"]),
        "name_masked": result["name_masked"],
        "phone_masked": result["phone_masked"],
        "class_org_unit_id": result.get("class_org_unit_id"),
        "class_name": result.get("class_name"),
        "study_group_org_unit_id": result.get("study_group_org_unit_id"),
        "study_group_name": result.get("study_group_name"),
    }


def _require_binding_enabled() -> None:
    if not get_settings().wechat_member_binding_enabled:
        raise WeChatIdentityError("微信学员身份功能尚未开启")


def exchange_wechat_code(code: str) -> dict[str, str]:
    """Exchange a wx.login code without logging the code or returned openid."""

    settings = get_settings()
    cleaned = (code or "").strip()
    if not cleaned or len(cleaned) > 512:
        raise WeChatProviderError("微信登录凭证无效")
    # The local UX acceptance flow cannot call the production WeChat provider
    # from an isolated localhost API. Keep a deterministic, dev/test-only
    # stub; startup safety rejects this flag in every deployable environment.
    if settings.wechat_local_test_mode and settings.app_env in {"dev", "test"}:
        return {
            "appid": settings.wechat_miniprogram_app_id or "local-test-app",
            "openid": "local-test-openid",
        }
    if not settings.wechat_miniprogram_app_id or not settings.wechat_miniprogram_app_secret:
        raise WeChatProviderError("微信小程序身份服务尚未配置")
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": settings.wechat_miniprogram_app_id,
                    "secret": settings.wechat_miniprogram_app_secret,
                    "js_code": cleaned,
                    "grant_type": "authorization_code",
                },
            )
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise WeChatProviderError("微信身份服务暂时不可用，请稍后重试") from exc
    if response.status_code != 200 or data.get("errcode") or not data.get("openid"):
        raise WeChatProviderError("微信身份服务暂时不可用，请稍后重试")
    return {
        "appid": settings.wechat_miniprogram_app_id,
        "openid": str(data["openid"]),
    }


def verify_member_binding(*, code: str, name: str, phone: str) -> dict[str, Any]:
    """Match one active member and create/update exactly one app binding."""

    _require_binding_enabled()
    cleaned_name = (name or "").strip()
    if not cleaned_name or len(cleaned_name) > 120:
        raise WeChatIdentityError("暂时无法完成身份匹配，请联系工作人员")
    try:
        normalized_phone = normalize_phone(phone)
        hashed_phone = phone_hash(normalized_phone)
    except ValueError as exc:
        raise WeChatIdentityError("暂时无法完成身份匹配，请联系工作人员") from exc

    identity = exchange_wechat_code(code)
    appid = identity.get("appid") or get_settings().wechat_miniprogram_app_id
    openid = identity.get("openid")
    if not appid or not openid:
        raise WeChatProviderError("微信身份服务暂时不可用，请稍后重试")

    # Deliberately fetch at most two rows so duplicate data does not become an
    # enumeration oracle.  The response is the same for zero and many matches.
    matches = fetch_all(
        "SELECT id FROM members WHERE name=? AND phone_hash=? AND status='ACTIVE' LIMIT 2",
        (cleaned_name, hashed_phone),
    )
    if len(matches) != 1:
        raise WeChatIdentityError("暂时无法完成身份匹配，请联系工作人员")
    member_id = int(matches[0]["id"])

    # Isolated UX acceptance simulates distinct WeChat accounts when switching
    # fixture people in a single developer tool. Never alter real provider IDs.
    settings = get_settings()
    if settings.wechat_local_test_mode and settings.app_env in {"dev", "test"}:
        openid = f"local-test-member-{member_id}"

    with transaction() as connection:
        now = _db_timestamp(connection)
        existing_openid = execute(
            connection,
            "SELECT id, member_id, status FROM wechat_member_bindings "
            "WHERE appid=? AND openid=? LIMIT 1",
            (appid, openid),
        ).fetchone()
        existing_member = execute(
            connection,
            "SELECT id, member_id, openid, status FROM wechat_member_bindings "
            "WHERE appid=? AND member_id=? AND status='VERIFIED' LIMIT 1",
            (appid, member_id),
        ).fetchone()
        if existing_openid and int(existing_openid["member_id"]) != member_id:
            raise WeChatIdentityError("暂时无法完成身份匹配，请联系工作人员")
        if existing_member and existing_member["openid"] != openid:
            # One effective binding per AppID/member.  A revoked row is
            # reactivated with the same openid; changing openid requires an
            # explicit revoke/rebind operation instead of silently stealing a
            # member's existing session.
            raise WeChatIdentityError("该学员已有微信绑定，请先联系工作人员解绑")

        if existing_openid:
            binding_id = int(existing_openid["id"])
            execute(
                connection,
                "UPDATE wechat_member_bindings SET member_id=?, status='VERIFIED', active_slot=1, "
                "verified_at=?, revoked_at=NULL, updated_at=? WHERE id=?",
                (member_id, now, now, binding_id),
            )
        else:
            cursor = execute(
                connection,
                "INSERT INTO wechat_member_bindings "
                "(appid, openid, member_id, status, binding_source, verified_at, "
                "active_slot, created_at, updated_at) VALUES (?, ?, ?, 'VERIFIED', "
                "'MINIPROGRAM_SELF_SERVICE', ?, 1, ?, ?)",
                (appid, openid, member_id, now, now, now),
            )
            binding_id = int(cursor.lastrowid)
        write_audit(
            connection,
            actor_user_id=None,
            action="wechat.member_binding.verify",
            resource_type="wechat_member_binding",
            resource_id=str(binding_id),
            after={"member_id": member_id, "status": "VERIFIED", "appid": appid},
        )
        member = _member_payload(connection, member_id)
    if not member:
        raise WeChatIdentityError("暂时无法完成身份匹配，请联系工作人员")
    token = create_token(
        binding_id,
        1,
        WECHAT_SESSION_TOKEN_TYPE,
        timedelta(days=7),
    )
    return {
        "access_token": token,
        "expires_in": 7 * 24 * 60 * 60,
        "member": member,
    }


def resolve_member_session(token: str) -> dict[str, Any]:
    _require_binding_enabled()
    try:
        payload = decode_token(token, WECHAT_SESSION_TOKEN_TYPE)
        binding_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise WeChatIdentityError("微信身份登录已失效，请重新绑定") from exc
    connection = None
    try:
        from app.db import connect

        connection = connect()
        row = execute(
            connection,
            "SELECT b.id AS binding_id, b.appid, b.member_id, m.status AS member_status "
            "FROM wechat_member_bindings b JOIN members m ON m.id=b.member_id "
            "WHERE b.id=? AND b.status='VERIFIED' AND m.status='ACTIVE' LIMIT 1",
            (binding_id,),
        ).fetchone()
        if not row:
            raise WeChatIdentityError("微信身份登录已失效，请重新绑定")
        member = _member_payload(connection, int(row["member_id"]))
    finally:
        if connection is not None:
            connection.close()
    if not member:
        raise WeChatIdentityError("微信身份登录已失效，请重新绑定")
    return {
        "binding_id": int(row["binding_id"]),
        "appid": row["appid"],
        "member_id": int(row["member_id"]),
        "member": member,
    }


def revoke_member_binding(token: str) -> dict[str, Any]:
    _require_binding_enabled()
    session = resolve_member_session(token)
    with transaction() as connection:
        now = _db_timestamp(connection)
        execute(
            connection,
            "UPDATE wechat_member_bindings SET status='REVOKED', revoked_at=?, updated_at=? "
            ", active_slot=NULL WHERE id=? AND status='VERIFIED'",
            (now, now, session["binding_id"]),
        )
        write_audit(
            connection,
            actor_user_id=None,
            action="wechat.member_binding.revoke",
            resource_type="wechat_member_binding",
            resource_id=str(session["binding_id"]),
            after={"member_id": session["member_id"], "status": "REVOKED"},
        )
    return {"revoked": True}


def get_member_role_scopes(member_id: int) -> list[dict[str, Any]]:
    """Resolve current capabilities from formal appointments and legacy roles.

    The returned ``role_key`` values intentionally remain the internal
    GROUP_LEADER/CLASS_COUNSELOR values used by the V1.2 storage contract.
    ``position_key``/``scope_level`` preserve the configured volunteer position
    that produced the capability for audit and UI explanations.
    """

    settings = get_settings()
    if not settings.identity_authorization_enabled:
        return []
    now = _now()
    scopes: list[dict[str, Any]] = []
    try:
        canonical = fetch_all(
            "SELECT va.appointment_key, va.org_unit_id, va.scope_type, "
            "c.position_name, c.scope_level, pc.capability_key "
            "FROM member_identities mi JOIN person_profiles pp ON pp.id=mi.person_id "
            "JOIN volunteer_appointments va ON va.person_id=mi.person_id "
            "JOIN volunteer_position_catalog c ON c.position_key=va.appointment_key "
            "JOIN volunteer_position_capabilities pc ON pc.position_key=c.position_key "
            "WHERE mi.member_id=? AND mi.status='ACTIVE' AND pp.status='ACTIVE' "
            "AND c.is_active=1 AND pc.capability_key=? "
            "AND va.status IN ('PLANNED','ACTIVE') AND va.starts_at<=? "
            "AND (va.ends_at IS NULL OR va.ends_at>=?)",
            (member_id, STUDY_MEETING_MANAGE, now, now),
        )
    except Exception as exc:
        # During a rolling migration, keep the old two-role resolver available
        # until 0039 is present.  Other database failures must still surface.
        message = str(exc).lower()
        if "no such table" not in message and "doesn't exist" not in message:
            raise
        canonical = fetch_all(
            "SELECT va.appointment_key, va.org_unit_id, va.scope_type "
            "FROM member_identities mi JOIN person_profiles pp ON pp.id=mi.person_id "
            "JOIN volunteer_appointments va ON va.person_id=mi.person_id "
            "WHERE mi.member_id=? AND mi.status='ACTIVE' AND pp.status='ACTIVE' "
            "AND va.appointment_key IN ('volunteer_group_leader','volunteer_class_counselor') "
            "AND va.status IN ('PLANNED','ACTIVE') AND va.starts_at<=? "
            "AND (va.ends_at IS NULL OR va.ends_at>=?)",
            (member_id, now, now),
        )
    for row in canonical:
        scope_level = row.get("scope_level")
        if not scope_level:
            scope_level = (
                "CLASS" if row["appointment_key"] == "volunteer_class_counselor"
                else "GROUP" if row["appointment_key"] == "volunteer_group_leader"
                else None
            )
        role_key = (
            "CLASS_COUNSELOR" if scope_level == "CLASS"
            else "GROUP_LEADER" if scope_level == "GROUP"
            else None
        )
        if role_key:
            scopes.append(
                {
                    "role_key": role_key,
                    "position_key": row["appointment_key"],
                    "position_name": row.get("position_name")
                    or (
                        "班主任" if scope_level == "CLASS" else "组长"
                    ),
                    "scope_level": scope_level,
                    "capability_key": row.get("capability_key"),
                    "scope_type": row["scope_type"],
                    "org_unit_id": row["org_unit_id"],
                }
            )

    # Legacy account roles are accepted only when the account is explicitly
    # linked to this member and has an explicit UNIT/SUBTREE data scope.
    legacy = fetch_all(
        "SELECT ur.role_key, ds.scope_type, ds.org_unit_id "
        "FROM app_users au JOIN user_roles ur ON ur.user_id=au.id "
        "JOIN data_scope_grants ds ON ds.user_id=au.id "
        "WHERE au.member_id=? AND au.is_active=1 "
        "AND ur.role_key IN ('group_leader','class_counselor') "
        "AND (ur.valid_from IS NULL OR ur.valid_from<=?) "
        "AND (ur.valid_until IS NULL OR ur.valid_until>=?) "
        "AND ds.scope_type IN ('UNIT','SUBTREE') "
        "AND (ds.valid_from IS NULL OR ds.valid_from<=?) "
        "AND (ds.valid_until IS NULL OR ds.valid_until>=?)",
        (member_id, now, now, now, now),
    )
    for row in legacy:
        role_key = WECHAT_BINDING_ROLES.get(row["role_key"])
        if role_key and row.get("org_unit_id"):
            scopes.append(
                {
                    "role_key": role_key,
                    "scope_type": row["scope_type"],
                    "org_unit_id": row["org_unit_id"],
                }
            )
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in scopes:
        unique[
            (
                item["role_key"],
                item.get("position_key"),
                item["scope_type"],
                item["org_unit_id"],
            )
        ] = item
    return list(unique.values())


def scope_contains(scope: dict[str, Any], org_unit_id: str) -> bool:
    if scope.get("scope_type") == "UNIT":
        return scope.get("org_unit_id") == org_unit_id
    if scope.get("scope_type") != "SUBTREE" or not scope.get("org_unit_id"):
        return False
    row = fetch_one(
        "WITH RECURSIVE descendants(id) AS ("
        " SELECT id FROM org_units WHERE id=? "
        " UNION ALL SELECT o.id FROM org_units o JOIN descendants d ON o.parent_id=d.id"
        ") SELECT id FROM descendants WHERE id=? LIMIT 1",
        (scope["org_unit_id"], org_unit_id),
    )
    return bool(row)


def role_for_target(member_id: int, class_org_unit_id: str, group_org_unit_id: str) -> str | None:
    """Resolve the strongest permitted role for one class/group target."""

    scopes = get_member_role_scopes(member_id)
    if any(
        item["role_key"] == "CLASS_COUNSELOR"
        and (
            scope_contains(item, class_org_unit_id)
            if item.get("scope_level") == "CLASS"
            else scope_contains(item, class_org_unit_id)
            or scope_contains(item, group_org_unit_id)
        )
        for item in scopes
    ):
        return "CLASS_COUNSELOR"
    if any(
        item["role_key"] == "GROUP_LEADER"
        and scope_contains(item, group_org_unit_id)
        for item in scopes
    ):
        return "GROUP_LEADER"
    return None


def authorized_group_targets(member_id: int) -> list[dict[str, Any]]:
    """Resolve active classes/groups from org master data and role scopes."""

    now = _now()
    groups = fetch_all(
        "SELECT g.id AS group_org_unit_id, g.name AS group_name, "
        "c.id AS class_org_unit_id, c.name AS class_name "
        "FROM org_units g JOIN org_units c ON c.id=g.parent_id "
        "WHERE g.unit_type='GROUP' AND g.is_active=1 AND c.unit_type='CLASS' AND c.is_active=1 "
        "ORDER BY c.name, g.name, g.id"
    )
    # A scope check is performed against the current org tree for every target;
    # no class/group names or ids are taken from client input.
    scopes = get_member_role_scopes(member_id)
    result: list[dict[str, Any]] = []
    for row in groups:
        role: str | None = None
        matched_scope: dict[str, Any] | None = None
        for item in scopes:
            if item["role_key"] != "CLASS_COUNSELOR":
                continue
            in_scope = (
                scope_contains(item, row["class_org_unit_id"])
                if item.get("scope_level") == "CLASS"
                else scope_contains(item, row["class_org_unit_id"])
                or scope_contains(item, row["group_org_unit_id"])
            )
            if in_scope:
                role = "CLASS_COUNSELOR"
                matched_scope = item
                break
        if role is None:
            for item in scopes:
                if item["role_key"] == "GROUP_LEADER" and scope_contains(
                    item, row["group_org_unit_id"]
                ):
                    role = "GROUP_LEADER"
                    matched_scope = item
                    break
        if role:
            result.append(
                {
                    **dict(row),
                    "role_key": role,
                    # This is a display-only label for the mini-program; the
                    # capability and scope checks above remain authoritative.
                    "position_name": (
                        matched_scope.get("position_name")
                        if matched_scope
                        else None
                    )
                    or ("班主任" if role == "CLASS_COUNSELOR" else "组长"),
                }
            )
    return result
