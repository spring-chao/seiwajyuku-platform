from __future__ import annotations

from typing import Any

from app.services.followup_invitations import can_participate


def can_view_followup_task_metadata(
    task: dict[str, Any],
    actor_user_id: int,
    allowed_org_ids: set[str] | None,
) -> bool:
    """Apply the same organization and confidentiality rules everywhere.

    Access to a member through a class or group relation does not grant access
    to every follow-up task attached to that member.  Task lists, member
    timelines and derived service signals must all fail closed on the task's
    own organization scope first.
    """
    if allowed_org_ids is not None and task["org_unit_id"] not in allowed_org_ids:
        return False
    confidentiality = str(task.get("confidentiality_level") or "").upper()
    if confidentiality == "ASSIGNEE":
        return can_participate(task["id"], actor_user_id)
    return confidentiality == "ORG_MANAGERS"
