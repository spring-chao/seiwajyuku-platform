from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.settings import get_settings
from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids, user_context


INVITATION_TYPES = {"ASSIGNEE", "COMPANION"}
PENDING_STATES = {"PENDING", "ADJUSTMENT_REQUESTED"}


def _as_utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _enabled() -> None:
    if not get_settings().volunteer_service_invitations_enabled:
        raise PermissionError("志工服务邀请功能尚未启用")


def invitation_capabilities() -> dict[str, bool]:
    return {
        "enabled": get_settings().volunteer_service_invitations_enabled,
        "production_mutations_approved": (
            not get_settings().is_production
            or get_settings().allow_production_mutations
        ),
    }


def validate_invitee(
    invited_user_id: int,
    org_unit_id: str,
    *,
    member_id: int | None = None,
) -> dict[str, Any]:
    user = fetch_one(
        "SELECT id, display_name, is_active FROM app_users WHERE id=?",
        (invited_user_id,),
    )
    context = user_context(invited_user_id)
    if not user or not user["is_active"] or not context:
        raise ValueError("受邀人账号当前不可用")
    if "followups:manage" not in context["permissions"]:
        raise ValueError("受邀人当前任职不包含本服务事项权限")
    allowed = accessible_org_ids(invited_user_id)
    if allowed is not None and org_unit_id not in allowed:
        # A task is normally scoped to the member's primary center, while a
        # class/group volunteer may be appointed only to a formal child
        # relation.  When the task carries its member id, resolve that formal
        # relation instead of requiring the volunteer to hold center-wide
        # scope.  This keeps invitation checks consistent with member/task
        # visibility checks and prevents a class-scoped invitation from being
        # rejected merely because the task is stored at center level.
        if member_id is None:
            raise ValueError("受邀人的有效任职范围不包含该学长所属组织")
        from app.services.members import resolve_member_scope

        try:
            resolve_member_scope(member_id, org_unit_id, allowed)
        except PermissionError as exc:
            raise ValueError("受邀人的有效任职范围不包含该学长所属组织") from exc
    return user


def insert_invitation(
    connection,
    *,
    task: dict[str, Any],
    actor_user_id: int,
    invited_user_id: int,
    invitation_type: str,
    invitation_message: str | None,
    proposed_due_at: str | None,
    valid_until: str,
) -> int:
    invitation_type = invitation_type.upper()
    if invitation_type not in INVITATION_TYPES:
        raise ValueError("未知邀请类型")
    if _as_utc(valid_until) <= datetime.now(UTC):
        raise ValueError("邀请有效期必须晚于当前时间")
    validate_invitee(
        invited_user_id,
        task["org_unit_id"],
        member_id=task.get("member_id"),
    )
    active = execute(
        connection,
        "SELECT id FROM followup_service_invitations "
        "WHERE task_id=? AND invited_user_id=? AND invitation_type=? "
        "AND status IN ('PENDING','ADJUSTMENT_REQUESTED','ACCEPTED') LIMIT 1",
        (task["id"], invited_user_id, invitation_type),
    ).fetchone()
    if active:
        raise ValueError("该学长已收到同一服务事项的有效邀请")
    now = datetime.now(UTC).isoformat()
    cursor = execute(
        connection,
        "INSERT INTO followup_service_invitations"
        "(task_id, invitation_type, invited_user_id, invited_by_user_id, status, "
        "invitation_message, proposed_due_at, valid_until, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?)",
        (
            task["id"],
            invitation_type,
            invited_user_id,
            actor_user_id,
            invitation_message.strip() if invitation_message else None,
            proposed_due_at,
            valid_until,
            now,
            now,
        ),
    )
    invitation_id = cursor.lastrowid
    write_audit(
        connection,
        actor_user_id=actor_user_id,
        action="followups.invitation.create",
        resource_type="followup_service_invitation",
        resource_id=str(invitation_id),
        org_unit_id=task["org_unit_id"],
        after={
            "task_id": task["id"],
            "invitation_type": invitation_type,
            "invited_user_id": invited_user_id,
            "valid_until": valid_until,
        },
    )
    return invitation_id


def _task(task_id: int) -> dict[str, Any]:
    task = fetch_one("SELECT * FROM followup_tasks WHERE id=?", (task_id,))
    if not task:
        raise ValueError("服务事项不存在")
    return task


def _accepted_primary(task_id: int, user_id: int) -> bool:
    invitation = fetch_one(
        "SELECT status, valid_until FROM followup_service_invitations "
        "WHERE task_id=? AND invitation_type='ASSIGNEE' AND invited_user_id=? "
        "ORDER BY id DESC LIMIT 1",
        (task_id, user_id),
    )
    if not invitation:
        return _task(task_id)["assigned_user_id"] == user_id
    return invitation["status"] == "ACCEPTED"


def create_invitation(
    task_id: int,
    actor_user_id: int,
    *,
    invited_user_id: int,
    invitation_type: str,
    invitation_message: str | None,
    proposed_due_at: str | None,
    valid_until: str,
) -> int:
    _enabled()
    task = _task(task_id)
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and task["org_unit_id"] not in allowed:
        raise PermissionError("服务事项不在当前有效任职范围内")
    invitation_type = invitation_type.upper()
    if invitation_type == "ASSIGNEE":
        if task["created_by"] != actor_user_id:
            raise PermissionError("只有服务事项发起人可以邀请担当")
    elif invitation_type == "COMPANION":
        if not _accepted_primary(task_id, actor_user_id):
            raise PermissionError("接受担当后才可以邀请同行伙伴")
    else:
        raise ValueError("未知邀请类型")
    with transaction() as connection:
        return insert_invitation(
            connection,
            task=task,
            actor_user_id=actor_user_id,
            invited_user_id=invited_user_id,
            invitation_type=invitation_type,
            invitation_message=invitation_message,
            proposed_due_at=proposed_due_at,
            valid_until=valid_until,
        )


def list_my_invitations(user_id: int) -> list[dict[str, Any]]:
    if not get_settings().volunteer_service_invitations_enabled:
        return []
    rows = fetch_all(
        "SELECT i.id, i.task_id, i.invitation_type, i.status, i.invitation_message, "
        "i.proposed_due_at, i.requested_due_at, i.response_note, i.valid_until, "
        "i.responded_at, i.created_at, t.member_id, m.name AS member_name, "
        "m.phone_masked, m.company_name, t.service_purpose, t.due_at, t.org_unit_id, "
        "o.name AS org_name, inviter.display_name AS inviter_name "
        "FROM followup_service_invitations i "
        "JOIN followup_tasks t ON t.id=i.task_id "
        "JOIN members m ON m.id=t.member_id "
        "JOIN org_units o ON o.id=t.org_unit_id "
        "JOIN app_users inviter ON inviter.id=i.invited_by_user_id "
        "WHERE i.invited_user_id=? ORDER BY i.created_at DESC, i.id DESC",
        (user_id,),
    )
    allowed = accessible_org_ids(user_id)
    if allowed is not None:
        from app.services.members import resolve_member_scope

        visible_rows = []
        for row in rows:
            if row["org_unit_id"] in allowed:
                visible_rows.append(row)
                continue
            try:
                resolve_member_scope(row["member_id"], row["org_unit_id"], allowed)
            except PermissionError:
                continue
            visible_rows.append(row)
        rows = visible_rows
    now = datetime.now(UTC)
    for row in rows:
        if row["status"] in PENDING_STATES and _as_utc(row["valid_until"]) <= now:
            row["status"] = "EXPIRED"
    return rows


def _invitation_for_response(invitation_id: int, actor_user_id: int) -> dict[str, Any]:
    invitation = fetch_one(
        "SELECT i.*, t.member_id, t.org_unit_id, t.status AS task_status "
        "FROM followup_service_invitations i "
        "JOIN followup_tasks t ON t.id=i.task_id WHERE i.id=?",
        (invitation_id,),
    )
    if not invitation:
        raise ValueError("服务邀请不存在")
    if invitation["invited_user_id"] != actor_user_id:
        raise PermissionError("只能回应发给自己的服务邀请")
    if invitation["status"] not in PENDING_STATES:
        raise ValueError("该服务邀请当前不能回应")
    if _as_utc(invitation["valid_until"]) <= datetime.now(UTC):
        raise ValueError("该服务邀请已过有效期")
    if invitation["task_status"] not in {"OPEN", "IN_PROGRESS"}:
        raise ValueError("服务事项已结束")
    validate_invitee(
        actor_user_id,
        invitation["org_unit_id"],
        member_id=invitation["member_id"],
    )
    return invitation


def accept_invitation(
    invitation_id: int, actor_user_id: int, response_note: str | None = None
) -> None:
    _enabled()
    invitation = _invitation_for_response(invitation_id, actor_user_id)
    if invitation["status"] != "PENDING":
        raise ValueError("请等待时间调整建议回应后再接受邀请")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "UPDATE followup_service_invitations SET status='ACCEPTED', response_note=?, "
            "responded_at=?, updated_at=? WHERE id=?",
            (response_note.strip() if response_note else None, now, now, invitation_id),
        )
        execute(
            connection,
            "INSERT INTO followup_collaborators"
            "(task_id, user_id, invitation_id, collaboration_role, status, starts_at, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?) "
            "ON CONFLICT(task_id, user_id, collaboration_role) DO UPDATE SET "
            "invitation_id=excluded.invitation_id, status='ACTIVE', ends_at=NULL, "
            "starts_at=excluded.starts_at, updated_at=excluded.updated_at"
            if get_settings().database_url.startswith("sqlite")
            else
            "INSERT INTO followup_collaborators"
            "(task_id, user_id, invitation_id, collaboration_role, status, starts_at, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?) "
            "ON DUPLICATE KEY UPDATE invitation_id=VALUES(invitation_id), status='ACTIVE', "
            "ends_at=NULL, starts_at=VALUES(starts_at), updated_at=VALUES(updated_at)",
            (
                invitation["task_id"],
                actor_user_id,
                invitation_id,
                invitation["invitation_type"],
                now,
                now,
                now,
            ),
        )
        if invitation["invitation_type"] == "ASSIGNEE":
            execute(
                connection,
                "UPDATE followup_tasks SET assigned_user_id=?, due_at=COALESCE(?, due_at), "
                "updated_at=? WHERE id=?",
                (
                    actor_user_id,
                    invitation["proposed_due_at"],
                    now,
                    invitation["task_id"],
                ),
            )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="followups.invitation.accept",
            resource_type="followup_service_invitation",
            resource_id=str(invitation_id),
            org_unit_id=invitation["org_unit_id"],
            after={
                "task_id": invitation["task_id"],
                "invitation_type": invitation["invitation_type"],
                "status": "ACCEPTED",
            },
        )


def request_adjustment(
    invitation_id: int,
    actor_user_id: int,
    *,
    requested_due_at: str,
    response_note: str,
) -> None:
    _enabled()
    invitation = _invitation_for_response(invitation_id, actor_user_id)
    if invitation["status"] != "PENDING":
        raise ValueError("时间调整建议已经送达，请等待发起人回应")
    if _as_utc(requested_due_at) <= datetime.now(UTC):
        raise ValueError("建议时间必须晚于当前时间")
    note = response_note.strip()
    if len(note) < 2:
        raise ValueError("请简要说明建议调整时间的原因")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "UPDATE followup_service_invitations SET status='ADJUSTMENT_REQUESTED', "
            "requested_due_at=?, response_note=?, responded_at=?, updated_at=? WHERE id=?",
            (requested_due_at, note, now, now, invitation_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="followups.invitation.adjustment_request",
            resource_type="followup_service_invitation",
            resource_id=str(invitation_id),
            org_unit_id=invitation["org_unit_id"],
            purpose=note,
            after={"requested_due_at": requested_due_at},
        )


def mark_unavailable(
    invitation_id: int, actor_user_id: int, response_note: str
) -> None:
    _enabled()
    invitation = _invitation_for_response(invitation_id, actor_user_id)
    note = response_note.strip()
    if len(note) < 2:
        raise ValueError("请简要说明本次暂时无法参与")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "UPDATE followup_service_invitations SET status='UNAVAILABLE', "
            "response_note=?, responded_at=?, updated_at=? WHERE id=?",
            (note, now, now, invitation_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="followups.invitation.unavailable",
            resource_type="followup_service_invitation",
            resource_id=str(invitation_id),
            org_unit_id=invitation["org_unit_id"],
            purpose=note,
            after={"status": "UNAVAILABLE"},
        )


def respond_to_adjustment(
    invitation_id: int,
    actor_user_id: int,
    *,
    proposed_due_at: str,
    response_note: str | None,
) -> None:
    _enabled()
    invitation = fetch_one(
        "SELECT i.*, t.org_unit_id, t.created_by FROM followup_service_invitations i "
        "JOIN followup_tasks t ON t.id=i.task_id WHERE i.id=?",
        (invitation_id,),
    )
    if not invitation:
        raise ValueError("服务邀请不存在")
    if invitation["status"] != "ADJUSTMENT_REQUESTED":
        raise ValueError("当前没有待回应的时间调整建议")
    if _as_utc(invitation["valid_until"]) <= datetime.now(UTC):
        raise ValueError("该服务邀请已过有效期")
    if actor_user_id not in {
        invitation["invited_by_user_id"],
        invitation["created_by"],
    }:
        raise PermissionError("只有邀请发起人可以回应时间调整建议")
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and invitation["org_unit_id"] not in allowed:
        raise PermissionError("服务事项不在当前有效任职范围内")
    if _as_utc(proposed_due_at) <= datetime.now(UTC):
        raise ValueError("建议完成时间必须晚于当前时间")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "UPDATE followup_service_invitations SET status='PENDING', proposed_due_at=?, "
            "response_note=?, responded_at=NULL, updated_at=? WHERE id=?",
            (
                proposed_due_at,
                response_note.strip() if response_note else None,
                now,
                invitation_id,
            ),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="followups.invitation.adjustment_response",
            resource_type="followup_service_invitation",
            resource_id=str(invitation_id),
            org_unit_id=invitation["org_unit_id"],
            after={"status": "PENDING", "proposed_due_at": proposed_due_at},
        )


def can_participate(task_id: int, user_id: int) -> bool:
    task = _task(task_id)
    primary = fetch_one(
        "SELECT status FROM followup_service_invitations "
        "WHERE task_id=? AND invitation_type='ASSIGNEE' AND invited_user_id=? "
        "ORDER BY id DESC LIMIT 1",
        (task_id, user_id),
    )
    if task["assigned_user_id"] == user_id and not primary:
        return True
    collaborator = fetch_one(
        "SELECT id FROM followup_collaborators "
        "WHERE task_id=? AND user_id=? AND status='ACTIVE' LIMIT 1",
        (task_id, user_id),
    )
    return collaborator is not None


def is_primary_assignee(task_id: int, user_id: int) -> bool:
    task = _task(task_id)
    return task["assigned_user_id"] == user_id and can_participate(task_id, user_id)
